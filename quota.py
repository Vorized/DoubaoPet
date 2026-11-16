# -*- coding: utf-8 -*-
"""火山方舟套餐额度后台服务：定时读取 Coding Plan / Agent Plan 的窗口用量。

数据源（config.QUOTA_SOURCE）：
- arkcli：调用 `arkcli usage plan --all --format json`，解析全部窗口
  （5h / weekly / monthly；推荐，走 SSO 登录，不需要 AK/SK）。
- api：用火山引擎 AK/SK + V4 签名直连 OpenAPI：
  Coding Plan → `GetCodingPlanUsage`（session / weekly / monthly，只有百分比），
  Agent Plan  → `GetAFPUsage`（5h / weekly / monthly，带 Quota / Used / ResetTime）。
- http：GET 自定义额度接口，接受扁平 JSON / arkcli JSON / 官方 API JSON。
- file：读取本地 quota.json（手动更新，或由其他脚本写入）。
- auto：arkcli → api → file 依次选择。

多窗口语义（重要）：
- 火山方舟的两个套餐都按多个窗口计量，真正会先卡住用户的是 session / 5 小时窗口；
- 快照里保留全部窗口（QuotaSnapshot.windows），顶层 used_percent 取「最紧窗口」
  （已用百分比最高，打平取更短的窗口），UI 标题标出窗口名；
- 低额度提醒、重置检测、变化检测都基于主窗口。

设计要点：
- 独立 QThread，阻塞请求 / 子进程都不占用 GUI 线程；
- 百分比 / 用量一律转 float 后再做展示，接口原始值不直接参与 UI；
- 失败指数退避 3 → 6 → 12 → … → 300 秒；认证 / 配置 / 未开通类错误停止自动重试，
  但点击刷新仍可重新尝试；网络类临时错误一律继续退避重试；
- 窗口跨周期重置、额度消耗、低额度跨越阈值都通过信号通知 UI。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from PySide6.QtCore import QThread, Signal

import config

log = logging.getLogger("pet.quota")

MIN_INTERVAL_SEC = 30       # 轮询间隔下限保护（秒）
BACKOFF_START_SEC = 3       # 失败退避起点（秒）
BACKOFF_MAX_SEC = 300       # 失败退避上限（秒）
MIN_REFRESH_GAP_SEC = 5     # 健康状态下两次手动刷新的最小间隔（防抖）

CN_TZ = timezone(timedelta(hours=8))
VOLC_HOST = "open.volcengineapi.com"
VOLC_API_VERSION = "2024-01-01"
VOLC_API_ACTION = "GetCodingPlanUsage"      # 个人版 Coding Plan
VOLC_AFP_ACTION = "GetAFPUsage"             # 个人版 Agent Plan（AFP）
PLAN_CHECK_TTL_SEC = 600                    # 「未开通」复核（arkcli plans get）结果缓存
# 测试脚本可临时改写这个模块变量，指向本地 mock（HTTP 数据源）
HTTP_QUOTA_URL = ""

# ============================ 额度窗口 ============================
#
# 真实接口形态（已用本机 arkcli --debug / 社区实测比对）：
#   Coding Plan（GetCodingPlanUsage）：Result.QuotaUsage[] Level = session / weekly / monthly
#   Agent Plan （GetAFPUsage）        ：Result.AFPFiveHour / AFPWeekly / AFPMonthly（+ AFPDaily）
#   arkcli `usage plan`               ：items[].periods[] label = 5h / weekly / monthly
# AFPDaily 按控制台惯例跳过（两个社区实现都不展示日窗口）。
WINDOW_ORDER = {"5h": 0, "session": 1, "weekly": 2, "monthly": 3}
WINDOW_LABELS = {"5h": "5 小时", "session": "会话", "weekly": "周", "monthly": "月"}
WINDOW_SHORT = {"5h": "5h", "session": "会话", "weekly": "周", "monthly": "月"}
_WINDOW_ALIASES = {
    "5h": "5h", "5hour": "5h", "5hours": "5h", "fivehour": "5h", "fivehours": "5h",
    "5小时": "5h", "五小时": "5h",
    "session": "session", "rolling": "session", "会话": "session",
    "weekly": "weekly", "week": "weekly", "7d": "weekly", "周": "weekly",
    "monthly": "monthly", "month": "monthly", "30d": "monthly", "月": "monthly",
    "daily": "daily", "day": "daily", "日": "daily",
}


# ============================ 数据结构 ============================


@dataclass(frozen=True)
class QuotaWindow:
    """套餐的一个计量窗口（session / 5h / weekly / monthly）。"""

    key: str                     # 5h / session / weekly / monthly
    used_percent: float          # 已用百分比（0~100，可能略超 100）
    used: float | None = None    # 绝对已用量（Coding Plan 只有百分比，为 None）
    total: float | None = None
    reset_at: datetime | None = None

    @property
    def label(self) -> str:
        return WINDOW_LABELS.get(self.key, self.key)

    @property
    def short(self) -> str:
        return WINDOW_SHORT.get(self.key, self.key)

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)


@dataclass(frozen=True)
class QuotaSnapshot:
    """一次成功读取的额度快照。

    used_percent / used / total / reset_at 是**主窗口**（最紧窗口）的数值；
    全部窗口的明细在 windows 里。没有 windows 的数据源（扁平 JSON）沿用原行为。
    """

    product: str                 # coding-plan / agent-plan / custom
    product_name: str            # Coding Plan / Agent Plan / 豆包额度
    tier: str                    # lite / pro / small / medium / ...（可能为空）
    used_percent: float          # 主窗口已用百分比（0~100，可能略超 100）
    fetched_at: datetime
    source: str                  # arkcli / api / http / file
    source_label: str            # SSO / AK/SK / 本地文件 / 自定义接口
    used: float | None = None
    total: float | None = None
    unit: str = ""
    reset_at: datetime | None = None
    updated_at: datetime | None = None
    status: str = ""
    demo: bool = False
    windows: tuple[QuotaWindow, ...] = ()   # 全部窗口（按接口返回顺序）
    window_key: str = ""                    # 主窗口 key
    window_label: str = ""                  # 主窗口展示名（"5 小时"/"会话"/"周"/"月"）

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)

    @property
    def primary_window(self) -> QuotaWindow | None:
        for win in self.windows:
            if win.key == self.window_key:
                return win
        return None


def _window_key(raw: Any) -> str | None:
    """把 label / Level 归一化成窗口 key（容忍 5h / FiveHour / 周 / monthly(30d)）。"""
    text = str(raw or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if not text:
        return None
    if text in _WINDOW_ALIASES:
        return _WINDOW_ALIASES[text]
    for alias, key in _WINDOW_ALIASES.items():
        if alias in text:
            return key
    return None


def _first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _select_primary(windows: tuple[QuotaWindow, ...] | list[QuotaWindow]) -> QuotaWindow | None:
    """最紧窗口 = 已用百分比最高的窗口；打平时窗口越短越优先（5h > 会话 > 周 > 月）。"""
    usable = [win for win in windows if win.key in WINDOW_ORDER]
    if not usable:
        return None
    return max(usable, key=lambda win: (win.used_percent, -WINDOW_ORDER[win.key]))


def _finalize(snapshot: QuotaSnapshot) -> QuotaSnapshot:
    """把主窗口的数值提升到快照顶层：UI / 托盘 / 变化检测都读顶层字段。"""
    primary = _select_primary(snapshot.windows)
    if primary is None:
        return snapshot
    return replace(
        snapshot,
        used_percent=primary.used_percent,
        used=primary.used,
        total=primary.total,
        reset_at=primary.reset_at,
        window_key=primary.key,
        window_label=primary.label,
    )


def _looks_unprovisioned(snapshot: QuotaSnapshot) -> bool:
    """所有窗口都是 0% 且没有任何绝对用量 —— 需要进一步确认是否"根本没开通"。"""
    if not snapshot.windows:
        return False
    if any(win.used_percent > 0 for win in snapshot.windows):
        return False
    return all(not win.used for win in snapshot.windows)


@dataclass(frozen=True)
class QuotaChange:
    """两次成功刷新之间的额度变化（气泡变化行用）。"""

    kind: str                    # "use"（消耗）/ "reset"（重置）/ "adjust"（手动修正）
    delta_percent: float         # 变化百分比（正数 = 已用增加）
    remaining_percent: float
    message: str


class QuotaError(Exception):
    """带错误类别与重试策略的异常。

    retry_after：
      - None：由服务按指数退避决定；
      - -1  ：认证 / 配置 / 未订阅类错误，停止自动重试（手动刷新仍可重试）；
      - >=0 ：指定秒数后重试。
    """

    def __init__(self, kind: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


# ============================ 解析工具 ============================


def _as_float(value: Any, field: str) -> float | None:
    """把接口里的数字 / 字符串安全转成 float；空值返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise QuotaError("parse", f"{field} 不是有效数字：{value!r}")
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "").replace("%", "")
        return float(value)
    except (TypeError, ValueError) as exc:
        raise QuotaError("parse", f"{field} 不是有效数字：{value!r}") from exc


def _as_datetime(value: Any) -> datetime | None:
    """解析 RFC3339 字符串或 Unix 时间戳（秒 / 毫秒），统一为北京时间。"""
    if value is None or value == "" or value == -1:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts <= 0:
                return None
            if ts > 10_000_000_000:  # 毫秒
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(CN_TZ)
        text = str(value).strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return _as_datetime(float(text))
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return dt.astimezone(CN_TZ)
    except (TypeError, ValueError, OSError) as exc:
        raise QuotaError("parse", f"时间字段格式异常：{value!r}") from exc


def _as_datetime_soft(value: Any) -> datetime | None:
    """展示用的时间字段：解析失败只记一条警告，不让整次同步失败。"""
    try:
        return _as_datetime(value)
    except QuotaError as exc:
        log.warning("忽略无法解析的时间字段：%s", exc.message)
        return None


def _soft_float(value: Any) -> float | None:
    """可选的数字字段（used / total）：非法值当作缺失，而不是解析失败。"""
    try:
        return _as_float(value, "")
    except QuotaError:
        return None


def _extract_json(text: str) -> Any:
    """从 arkcli 输出中提取 JSON（容忍 ANSI 颜色码 / 前后的日志行）。"""
    if not text or not text.strip():
        raise QuotaError("parse", "arkcli 没有返回内容")
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    start = clean.find("{")
    end = clean.rfind("}")
    if start < 0 or end <= start:
        raise QuotaError("parse", f"无法从 arkcli 输出中解析 JSON：{clean[:120]}")
    try:
        return json.loads(clean[start:end + 1])
    except json.JSONDecodeError as exc:
        raise QuotaError("parse", f"arkcli 输出 JSON 解析失败：{exc}") from exc


def _source_label(auth_method: str) -> str:
    return {
        "sso": "SSO",
        "oauth": "SSO",
        "aksk": "AK/SK",
        "apikey": "API Key",
        "none": "未登录",
    }.get(str(auth_method or "").lower(), "arkcli")


def _product_name(product: str, tier: str = "") -> str:
    names = {
        "coding-plan": "Coding Plan",
        "coding-plan-team": "Coding Plan 团队版",
        "agent-plan": "Agent Plan",
        "agent-plan-team": "Agent Plan 团队版",
        "custom": "豆包额度",
    }
    name = names.get(product, "豆包额度")
    if tier:
        name += f" · {tier.upper() if tier.lower() in ('lite', 'pro') else tier}"
    return name


def _raise_on_response_error(payload: dict[str, Any]) -> None:
    """火山 OpenAPI 的错误统一在 ResponseMetadata.Error 里。"""
    meta = payload.get("ResponseMetadata")
    if isinstance(meta, dict) and meta.get("Error"):
        err = meta["Error"]
        code = str(err.get("Code") or "")
        message = str(err.get("Message") or err)
        auth_like = any(k in (code + message).lower()
                        for k in ("auth", "signature", "credential", "accesskey", "forbidden"))
        raise QuotaError("auth" if auth_like else "http",
                         f"火山 OpenAPI 返回错误：{code} {message}".strip(),
                         -1 if auth_like else None)


def _build_windows(entries: Any) -> list[QuotaWindow]:
    """把 QuotaUsage[] / arkcli periods[] 转成窗口列表。

    认 label / Level 两种字段名，percent / Percent 两种写法；日窗口按惯例跳过。
    """
    if not isinstance(entries, list):
        return []
    windows: list[QuotaWindow] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        key = _window_key(_first_value(item, ("label", "Level", "level", "window")))
        if key is None or key == "daily" or key in seen:
            continue
        percent = _soft_float(_first_value(item, ("percent", "Percent", "percentage")))
        if percent is None:
            continue
        seen.add(key)
        windows.append(QuotaWindow(
            key=key,
            used_percent=max(0.0, percent),
            used=_soft_float(_first_value(item, ("used", "Used"))),
            total=_soft_float(_first_value(item, ("total", "Total"))),
            reset_at=_as_datetime_soft(
                _first_value(item, ("reset_at", "ResetTimestamp", "ResetTime"))),
        ))
    return windows


def parse_coding_plan_payload(payload: Any) -> QuotaSnapshot:
    """解析 GetCodingPlanUsage（个人版 Coding Plan，只有百分比）。

    真实结构：
        {"Result": {"Status": "Running", "UpdateTimestamp": 0,
                    "QuotaUsage": [{"Level": "session|weekly|monthly",
                                    "Percent": 12.5, "ResetTimestamp": 0}]}}
    套餐被回收时只有 Status / UpdateTimestamp，没有 QuotaUsage。
    """
    if not isinstance(payload, dict):
        raise QuotaError("parse", "响应不是 JSON 对象")
    _raise_on_response_error(payload)

    result = payload.get("Result")
    if not isinstance(result, dict):
        result = payload
    status = str(result.get("Status") or "")
    entries = result.get("QuotaUsage")
    windows = _build_windows(entries)

    if not windows:
        if status and status.lower() not in ("running", "active", "normal", "ok"):
            raise QuotaError(
                "not_subscribed",
                f"Coding Plan 当前未生效（Status={status}），"
                "请到火山方舟控制台确认订阅状态",
                -1)
        if isinstance(entries, list):
            raise QuotaError("parse", "QuotaUsage 里没有可识别的额度窗口")
        raise QuotaError("parse", "GetCodingPlanUsage 响应缺少 QuotaUsage")

    snapshot = QuotaSnapshot(
        product="coding-plan",
        product_name=_product_name("coding-plan"),
        tier="",
        used_percent=0.0,          # 由 _finalize 用最紧窗口覆盖
        fetched_at=datetime.now(CN_TZ),
        source="api",
        source_label="AK/SK",
        updated_at=_as_datetime_soft(result.get("UpdateTimestamp")),
        status=status,
        windows=tuple(windows),
    )
    return _finalize(snapshot)


def parse_afp_payload(payload: Any, source: str = "api",
                      source_label: str = "AK/SK") -> QuotaSnapshot:
    """解析 GetAFPUsage（个人版 Agent Plan）。

    真实结构（ResetTime 为毫秒时间戳，-1 表示该窗口无重置）：
        {"Result": {"PlanType": "medium",
                    "AFPFiveHour": {"Quota": 10000, "Used": 0, "ResetTime": -1},
                    "AFPWeekly":  {...}, "AFPMonthly": {...}, "AFPDaily": {...}}}
    Quota == 0 表示该窗口没有额度（账号没开通），需要跳过而不是当成"用了 0%"。
    """
    if not isinstance(payload, dict):
        raise QuotaError("parse", "响应不是 JSON 对象")
    _raise_on_response_error(payload)

    result = payload.get("Result")
    if not isinstance(result, dict):
        raise QuotaError("parse", "GetAFPUsage 响应缺少 Result")

    plan_type = str(result.get("PlanType") or "")
    windows: list[QuotaWindow] = []
    for key, field in (("5h", "AFPFiveHour"), ("weekly", "AFPWeekly"), ("monthly", "AFPMonthly")):
        entry = result.get(field)
        if not isinstance(entry, dict):
            continue
        total = _soft_float(entry.get("Quota"))
        if not total or total <= 0:
            continue
        used = _soft_float(entry.get("Used")) or 0.0
        windows.append(QuotaWindow(
            key=key,
            used_percent=max(0.0, used / total * 100.0),
            used=used,
            total=total,
            reset_at=_as_datetime_soft(entry.get("ResetTime")),
        ))

    if not windows:
        raise QuotaError(
            "not_subscribed",
            "火山方舟账号没有生效中的 Agent Plan（AFP 额度为 0，PlanType 为空），"
            "请到火山方舟控制台确认订阅",
            -1)

    snapshot = QuotaSnapshot(
        product="agent-plan",
        product_name=_product_name("agent-plan", plan_type),
        tier=plan_type,
        used_percent=0.0,          # 由 _finalize 用最紧窗口覆盖
        fetched_at=datetime.now(CN_TZ),
        source=source,
        source_label=source_label,
        unit="点",
        status=plan_type,
        windows=tuple(windows),
    )
    return _finalize(snapshot)


def _rank_items(items: list[dict[str, Any]], preferred: str) -> list[dict[str, Any]]:
    """按套餐偏好排序：先个人版、指定产品优先，找不到时回退另一种套餐。"""
    preferred = str(preferred or "coding-plan").lower()

    def score(item: dict[str, Any]) -> tuple[int, int, int]:
        product = str(item.get("product") or "")
        edition = str(item.get("edition") or "")
        if preferred == "agent-plan":
            product_rank = 0 if product.startswith("agent-plan") else 1
        else:
            product_rank = 0 if product.startswith("coding-plan") else 1
        edition_rank = 0 if edition == "personal" else 1
        subscribed_rank = 0 if item.get("subscribed", True) else 1
        return product_rank, edition_rank, subscribed_rank

    return sorted(items, key=score)


_TRANSIENT_HINTS = (
    "timeout", "timed out", "i/o timeout", "dial tcp", "connection", "connect",
    "post \"", "get \"", "tls", "eof", "no such host", "context deadline",
    "reset by peer", "unavailable", "502", "503", "504", "429",
)


def _is_transient_tool_error(text: str) -> bool:
    """arkcli 把单次查询的失败原因塞进 items[].error；网络类要按可重试处理。"""
    lower = str(text or "").lower()
    return any(hint in lower for hint in _TRANSIENT_HINTS)


def parse_arkcli_payload(payload: Any, preferred_product: str = "coding-plan",
                         source: str = "arkcli",
                         source_label: str = "") -> QuotaSnapshot:
    """解析 `arkcli usage plan --all --format json` 的完整返回（全部窗口）。"""
    if not isinstance(payload, dict):
        raise QuotaError("parse", "arkcli 响应不是 JSON 对象")
    viewer = payload.get("viewer") if isinstance(payload.get("viewer"), dict) else {}
    if str(viewer.get("auth_method") or "").lower() == "none":
        raise QuotaError("auth",
                         "arkcli 尚未登录（右键桌宠 →「连接额度 / 登录」）", -1)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise QuotaError("not_subscribed",
                         "arkcli 未返回任何套餐（请先登录并订阅套餐）", -1)

    # subscribed 是可选字段（部分版本只在有套餐时返回 periods）；
    # false 明确跳过，true 或缺失但带 periods 都视为可用。
    subscribed = [
        i for i in items
        if isinstance(i, dict) and i.get("subscribed") is not False
        and (i.get("subscribed") is True or bool(i.get("periods")))
    ]
    if not subscribed:
        errors = [str(i.get("error")) for i in items
                  if isinstance(i, dict) and i.get("error")]
        detail = f"：{errors[0][:120]}" if errors else ""
        if any(_is_transient_tool_error(err) for err in errors):
            # 例如 GetAFPUsage 的 Post 超时：网络抖动不是"没订阅"，必须继续退避重试
            raise QuotaError("network", f"arkcli 查询额度遇到临时错误{detail}")
        raise QuotaError("not_subscribed",
                         f"火山方舟账号没有已开通的 Agent Plan / Coding Plan{detail}", -1)

    last_error = ""
    for item in _rank_items(subscribed, preferred_product):
        windows = _build_windows(item.get("periods"))
        if not windows:
            last_error = "该套餐没有可识别的额度窗口（periods 为空或字段变了）"
            continue
        product = str(item.get("product") or "custom")
        tier = str(item.get("tier") or "")
        snapshot = QuotaSnapshot(
            product=product,
            product_name=_product_name(product, tier),
            tier=tier,
            used_percent=0.0,      # 由 _finalize 用最紧窗口覆盖
            fetched_at=datetime.now(CN_TZ),
            source=source,
            source_label=source_label or _source_label(viewer.get("auth_method")),
            unit="点" if product.startswith("agent-plan") else "",
            updated_at=_as_datetime_soft(item.get("updated_at")),
            status=str(item.get("status") or ""),
            windows=tuple(windows),
        )
        return _finalize(snapshot)

    # 所有套餐都没有可用窗口：把 items[].error 分类后再决定是否重试
    errors = [str(i.get("error")) for i in subscribed
              if isinstance(i, dict) and i.get("error")]
    detail = f"：{errors[0][:120]}" if errors else ""
    if any(_is_transient_tool_error(err) for err in errors):
        raise QuotaError("network", f"arkcli 查询额度遇到临时错误{detail}")
    if errors:
        # 例如团队版没有绑定席位（需要管理员操作），重试也没用
        raise QuotaError("not_subscribed",
                         f"火山方舟套餐暂不可用（没有可用额度窗口）{detail}", -1)
    raise QuotaError("parse", last_error or "套餐中没有可用的额度数据")


def parse_flat_payload(payload: Any, source: str, source_label: str) -> QuotaSnapshot:
    """解析扁平额度 JSON（file / http 数据源）。"""
    if not isinstance(payload, dict):
        raise QuotaError("parse", "额度数据不是 JSON 对象")

    if isinstance(payload.get("items"), list):
        return parse_arkcli_payload(payload, config.QUOTA_PRODUCT,
                                    source=source, source_label=source_label)
    result = payload.get("Result")
    if isinstance(result, dict):
        if any(str(key).startswith("AFP") for key in result) or "PlanType" in result:
            return parse_afp_payload(payload, source=source, source_label=source_label)
        return replace(parse_coding_plan_payload(payload),
                       source=source, source_label=source_label)
    if isinstance(payload.get("QuotaUsage"), list):
        return replace(parse_coding_plan_payload(payload),
                       source=source, source_label=source_label)

    percent = None
    for key in ("percent", "used_percent", "usedPercent", "usage_percent"):
        if payload.get(key) is not None:
            percent = _as_float(payload.get(key), key)
            break

    used = _as_float(payload.get("used"), "used")
    total = _as_float(payload.get("total"), "total")
    if percent is None and used is not None and total not in (None, 0):
        percent = used / total * 100.0
    if percent is None:
        raise QuotaError("parse", "额度数据缺少 percent（或 used + total）字段")

    product = str(payload.get("product") or "custom")
    tier = str(payload.get("tier") or "")
    return QuotaSnapshot(
        product=product,
        product_name=str(payload.get("product_name") or _product_name(product, tier)),
        tier=tier,
        used_percent=percent,
        fetched_at=datetime.now(CN_TZ),
        source=source,
        source_label=str(payload.get("source_label") or source_label),
        used=used,
        total=total,
        unit=str(payload.get("unit") or ""),
        reset_at=_as_datetime_soft(payload.get("reset_at") or payload.get("ResetTimestamp")),
        updated_at=_as_datetime_soft(payload.get("updated_at") or payload.get("UpdateTimestamp")),
        status=str(payload.get("status") or ""),
        demo=bool(payload.get("demo", False)),
    )


# ============================ 展示工具 ============================


def format_percent(value: float | None) -> str:
    """百分比显示：37.4% / 62.6% / 100%（去掉多余的 .0）。"""
    if value is None:
        return "--"
    text = f"{value:.1f}"
    if text.endswith(".0"):
        text = text[:-2]
    return text + "%"


def format_number(value: float | None) -> str:
    """用量数字显示：整数加千分位，小数保留最多两位。"""
    if value is None:
        return "--"
    if abs(value - round(value)) < 0.005:
        return f"{value:,.0f}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def format_reset(reset_at: datetime | None, now: datetime | None = None) -> str:
    """把重置时间格式化成气泡提示。"""
    if reset_at is None:
        return ""
    now = now or datetime.now(CN_TZ)
    if reset_at <= now:
        return "已到重置时间，点击刷新"
    delta = reset_at - now
    if delta >= timedelta(days=1):
        left = f"剩 {delta.days} 天"
    elif delta >= timedelta(hours=1):
        left = f"剩 {int(delta.total_seconds() // 3600)} 小时"
    else:
        left = f"剩 {max(1, int(delta.total_seconds() // 60))} 分钟"
    return f"{reset_at.strftime('%m-%d %H:%M')} 重置（{left}）"


def format_snapshot(snap: QuotaSnapshot) -> str:
    """日志用的一行摘要。"""
    window = f"{snap.window_label}窗口 " if snap.window_label else ""
    text = (f"{snap.product_name} {window}已用 {format_percent(snap.used_percent)}"
            f"（剩余 {format_percent(snap.remaining_percent)}）")
    if snap.used is not None and snap.total is not None:
        amount = f"，{format_number(snap.used)} / {format_number(snap.total)}"
        if snap.unit:
            amount += f" {snap.unit}"
        text += amount
    if len(snap.windows) > 1:
        detail = " · ".join(f"{win.short} {format_percent(win.used_percent)}"
                            for win in snap.windows)
        text += f"，窗口：{detail}"
    if snap.reset_at is not None:
        text += f"，{snap.reset_at.strftime('%Y-%m-%d %H:%M')} 重置"
    if snap.demo:
        text += "（演示数据）"
    return text


def diff_snapshots(prev: QuotaSnapshot | None, cur: QuotaSnapshot) -> QuotaChange | None:
    """比较两次快照的主窗口已用百分比；套餐 / 主窗口变化或变化过小时返回 None。"""
    if prev is None or prev.demo != cur.demo or prev.product != cur.product:
        return None
    if (prev.window_key or "") != (cur.window_key or ""):
        # 主窗口换了（例如 5h 窗口重置后不再是最高），两份数据不可比，不弹变化
        return None

    window = f"{cur.window_label}窗口" if cur.window_label else "本月"
    remaining = cur.remaining_percent
    reset = False
    if prev.reset_at is not None and cur.reset_at is not None:
        reset = cur.reset_at > prev.reset_at + timedelta(hours=1)
    # 没有重置时间时，用“从较高用量直接掉到接近 0”兜底判断
    if not reset and prev.used_percent >= 20 and cur.used_percent <= 1:
        reset = True

    if reset:
        return QuotaChange(
            "reset", cur.used_percent - prev.used_percent, remaining,
            f"新周期的{window}额度已重置（已用 {format_percent(cur.used_percent)}）")

    delta = cur.used_percent - prev.used_percent
    if delta > 0.05:
        return QuotaChange("use", delta, remaining,
                           f"{window}额度消耗 +{format_percent(delta)}"
                           f"（剩余 {format_percent(remaining)}）")
    if delta < -0.05:
        return QuotaChange("adjust", delta, remaining,
                           f"{window}额度数据更新 {format_percent(delta)}"
                           f"（剩余 {format_percent(remaining)}）")
    return None


def is_low_quota(snap: QuotaSnapshot) -> bool:
    """当前剩余额度是否低于警示阈值。"""
    if not config.LOW_QUOTA_WARN_ENABLED:
        return False
    threshold = float(config.LOW_QUOTA_REMAINING_PERCENT)
    return threshold > 0 and snap.remaining_percent <= threshold


# ============================ 数据源解析 ============================


def quota_file_path() -> Path:
    path = Path(config.QUOTA_FILE)
    return path if path.is_absolute() else config.BASE_DIR / path


def volc_credentials() -> tuple[str, str]:
    """优先环境变量，其次 config；返回 (access_key, secret_key)。"""
    ak = os.environ.get("VOLC_ACCESS_KEY", "").strip() or str(config.VOLC_ACCESS_KEY).strip()
    sk = os.environ.get("VOLC_SECRET_KEY", "").strip() or str(config.VOLC_SECRET_KEY).strip()
    return ak, sk


def _resolve_arkcli() -> str | None:
    """找到 arkcli 可执行文件。

    GUI 程序由资源管理器启动时，PATH 可能还没有包含 npm 的全局 bin 目录
    （安装 / 更新 Node 包后尤其常见），所以除了 PATH 还检查 npm 默认位置。
    """
    command = list(config.ARKCLI_COMMAND)
    if not command:
        return None
    exe = os.path.expandvars(os.path.expanduser(str(command[0])))
    if os.path.isabs(exe):
        return exe if os.path.isfile(exe) else None
    found = shutil.which(exe)
    if found:
        return found
    if os.name == "nt":
        names = ([exe] if exe.lower().endswith((".cmd", ".exe", ".bat"))
                 else [exe + ".cmd", exe + ".exe", exe + ".bat"])
        roots = [
            Path(os.environ.get("APPDATA", "")) / "npm",
            Path(os.environ.get("ProgramFiles", "")) / "nodejs",
        ]
        for root in roots:
            if not str(root):
                continue
            for name in names:
                candidate = root / name
                if candidate.is_file():
                    return str(candidate)
    return None


def arkcli_executable() -> str | None:
    """返回 arkcli 可执行文件路径；未安装时返回 None（供认证菜单使用）。"""
    return _resolve_arkcli()


def _arkcli_available() -> bool:
    return _resolve_arkcli() is not None


def resolve_auto_source() -> str:
    """auto 模式的优先级：arkcli → AK/SK 直连 → 本地文件。"""
    if _arkcli_available():
        return "arkcli"
    ak, sk = volc_credentials()
    if ak and sk:
        return "api"
    if quota_file_path().exists():
        return "file"
    return "none"


def resolve_source() -> str:
    source = str(config.QUOTA_SOURCE or "auto").strip().lower()
    if source == "auto":
        return resolve_auto_source()
    return source if source in ("arkcli", "api", "http", "file") else "none"


# ============================ 火山 V4 签名 ============================


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}"
        for k, v in sorted(params.items())
    )


def volc_authorization(access_key: str, secret_key: str, region: str,
                       now: datetime | None = None,
                       action: str = VOLC_API_ACTION) -> tuple[str, str, str, str, str]:
    """生成火山引擎 OpenAPI V4 签名所需内容。

    返回 (timestamp, payload_hash, content_type, canonical_query, authorization)。
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp[:8]
    content_type = "application/x-www-form-urlencoded; charset=utf-8"
    body = b""
    payload_hash = _sha256_hex(body)
    signed_headers = "content-type;host;x-content-sha256;x-date"
    params = {"Action": action, "Version": VOLC_API_VERSION}
    query = _canonical_query(params)

    canonical_request = "\n".join([
        "POST",
        "/",
        query,
        f"content-type:{content_type}",
        f"host:{VOLC_HOST}",
        f"x-content-sha256:{payload_hash}",
        f"x-date:{timestamp}",
        "",
        signed_headers,
        payload_hash,
    ])
    scope = f"{date_stamp}/{region}/ark/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256",
        timestamp,
        scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])
    date_key = _hmac_sha256(secret_key.encode("utf-8"), date_stamp.encode("utf-8"))
    region_key = _hmac_sha256(date_key, region.encode("utf-8"))
    service_key = _hmac_sha256(region_key, b"ark")
    signing_key = _hmac_sha256(service_key, b"request")
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    authorization = (f"HMAC-SHA256 Credential={access_key}/{scope}, "
                     f"SignedHeaders={signed_headers}, Signature={signature}")
    return timestamp, payload_hash, content_type, query, authorization


def _volc_request(action: str, access_key: str, secret_key: str,
                  region: str, timeout: tuple) -> Any:
    """调用火山 OpenAPI（AK/SK V4 签名，POST 空 body）。"""
    timestamp, payload_hash, content_type, query, authorization = volc_authorization(
        access_key, secret_key, region, action=action)
    url = f"https://{VOLC_HOST}/?{query}"
    headers = {
        "Accept": "application/json",
        "Content-Type": content_type,
        "Host": VOLC_HOST,
        "X-Date": timestamp,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }
    try:
        resp = requests.post(url, data=b"", headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as exc:
        detail = str(exc).strip() or type(exc).__name__
        raise QuotaError("network", f"网络异常：{detail}") from exc

    if resp.status_code in (401, 403):
        body = (resp.text or "")[:160]
        raise QuotaError(
            "auth",
            f"火山 AK/SK 鉴权失败（HTTP {resp.status_code}）：请检查 Access Key / Secret Key "
            f"以及 ArkReadOnlyAccess 权限。{body}".strip(),
            -1)
    if resp.status_code != 200:
        body = (resp.text or resp.reason or "")[:200]
        raise QuotaError("http", f"HTTP {resp.status_code}：{body}")
    try:
        return resp.json()
    except ValueError as exc:
        raise QuotaError("parse", "火山 OpenAPI 返回的不是 JSON") from exc


def fetch_coding_plan_api(access_key: str, secret_key: str,
                          region: str, timeout: tuple) -> Any:
    """调用火山 OpenAPI GetCodingPlanUsage（个人版 Coding Plan）。"""
    return _volc_request(VOLC_API_ACTION, access_key, secret_key, region, timeout)


def fetch_afp_usage(access_key: str, secret_key: str,
                    region: str, timeout: tuple) -> Any:
    """调用火山 OpenAPI GetAFPUsage（个人版 Agent Plan）。"""
    return _volc_request(VOLC_AFP_ACTION, access_key, secret_key, region, timeout)


# ============================ 后台服务 ============================


class QuotaService(QThread):
    """后台套餐额度轮询线程。

    信号：
    - fetched(QuotaSnapshot)：一次成功获取。
    - failed(kind, message, retry_in_sec)：一次失败；retry_in_sec=-1 表示停止自动重试。
      kind: config / auth / not_subscribed / quota / network / http / parse / cli。
    - state_changed(state)：ok / backoff / auth_error / config_error /
      not_subscribed / stopped。
    - changed(QuotaChange)：额度消耗 / 重置 / 手动修正。
    - low_quota(bool)：低额度状态跨越阈值时发一次。
    """

    fetched = Signal(object)
    failed = Signal(str, str, int)
    state_changed = Signal(str)
    changed = Signal(object)
    low_quota = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        # 本地文件源没有网络成本，允许调到 2 秒（录制时按回车就能看到变化）；
        # 真实接口仍守住 30 秒下限，别把额度接口打挂。
        try:
            _floor = 2 if resolve_source() == "file" else MIN_INTERVAL_SEC
        except Exception:
            _floor = MIN_INTERVAL_SEC
        self._interval = max(_floor, int(config.POLL_INTERVAL_SEC))
        self._timeout = tuple(config.HTTP_TIMEOUT)
        self._wake = threading.Event()
        self._lock = threading.Lock()      # 保护 _fetching / _wake 的合并逻辑
        self._fetching = False             # 抓取进行中（期间的刷新请求会被合并）
        self._stop_requested = False
        self._state = ""
        self._last_ok_at = 0.0
        self._prev_snapshot: QuotaSnapshot | None = None
        self._low = False
        self._plan_check_at = 0.0          # 「未开通」复核结果缓存
        self._plan_check_ok: bool | None = None
        self._source = resolve_source()
        log.info("额度数据源：%s（配置为 %s）", self._source, config.QUOTA_SOURCE)

    # ------------------------- 外部控制 -------------------------

    def refresh_now(self) -> None:
        """请求立即刷新。

        - 抓取进行中：这次请求会被正在进行的抓取满足，不再叠加一次（避免重复抓取）；
        - 健康状态下距上次成功不足 5 秒：忽略（防抖）。
        """
        with self._lock:
            if self._fetching:
                log.debug("额度抓取进行中，本次刷新请求已合并")
                return
            if self._state == "ok" and (time.monotonic() - self._last_ok_at) < MIN_REFRESH_GAP_SEC:
                log.debug("刷新请求距上次成功不足 %d 秒，忽略", MIN_REFRESH_GAP_SEC)
                return
            self._wake.set()

    def stop(self) -> None:
        self._stop_requested = True
        self._wake.set()

    def source(self) -> str:
        return self._source

    # ------------------------------ 线程主体 ------------------------------

    def run(self) -> None:  # noqa: D102
        log.info("额度服务已启动：间隔 %d 秒，超时 %s 秒", self._interval, self._timeout)
        backoff = BACKOFF_START_SEC
        wait: float | None = 0          # 首轮立即抓取
        while not self._stop_requested:
            self._wake.wait(wait)
            with self._lock:
                self._wake.clear()
            if self._stop_requested:
                break
            with self._lock:
                self._fetching = True
            try:
                snapshot = self._fetch_once()
            except QuotaError as exc:
                if exc.retry_after == -1:
                    wait = None
                    state = ("auth_error" if exc.kind == "auth" else
                             "config_error" if exc.kind == "config" else
                             "not_subscribed" if exc.kind == "not_subscribed" else "stopped")
                    retry_in = -1
                else:
                    wait = exc.retry_after if exc.retry_after is not None else backoff
                    retry_in = int(wait)
                    state = "backoff"
                    backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                self._set_state(state)
                self.failed.emit(exc.kind, exc.message, retry_in)
            except Exception as exc:  # 兜底：任何未预期异常都不让线程静默退出
                log.exception("额度服务未预期异常")
                wait = backoff
                retry_in = int(wait)
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)
                self._set_state("backoff")
                self.failed.emit("unknown", f"未预期错误：{exc}", retry_in)
            else:
                backoff = BACKOFF_START_SEC
                self._last_ok_at = time.monotonic()
                try:
                    self._set_state("ok")
                    self.fetched.emit(snapshot)
                    change = diff_snapshots(self._prev_snapshot, snapshot)
                    self._prev_snapshot = snapshot
                    if change is not None:
                        self.changed.emit(change)
                    self._check_low_quota(snapshot)
                    wait = self._interval
                except Exception:
                    # 后处理出错不能让线程静默退出（pythonw 下连 traceback 都看不到）
                    log.exception("额度服务后处理异常，继续轮询")
                    wait = backoff
                    backoff = min(backoff * 2, BACKOFF_MAX_SEC)
            finally:
                with self._lock:
                    self._fetching = False

        self._set_state("stopped")
        log.info("额度服务已停止")

    # ------------------------------ 内部实现 ------------------------------

    def _fetch_once(self) -> QuotaSnapshot:
        if str(config.QUOTA_SOURCE).strip().lower() == "auto":
            source = resolve_auto_source()
            if source != self._source:
                log.info("额度数据源自动切换：%s → %s", self._source, source)
                self._source = source
        source = self._source
        if source == "arkcli":
            return self._fetch_arkcli()
        if source == "api":
            return self._fetch_api()
        if source == "http":
            return self._fetch_http()
        if source == "file":
            return self._fetch_file()
        raise QuotaError(
            "config",
            "未找到额度来源：请安装 arkcli、配置火山 AK/SK，或创建 quota.json（详见 README）",
            -1,
        )

    def _fetch_file(self) -> QuotaSnapshot:
        path = quota_file_path()
        if not path.exists():
            raise QuotaError("config",
                             f"额度文件不存在：{path}（可在 config.py 设置 QUOTA_SOURCE）",
                             -1)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise QuotaError("config", f"无法读取额度文件：{exc}", -1) from exc
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise QuotaError("parse", f"quota.json 解析失败：{exc}") from exc
        return parse_flat_payload(payload, "file", "本地文件")

    def _fetch_arkcli(self) -> QuotaSnapshot:
        command = list(config.ARKCLI_COMMAND)
        if not command:
            raise QuotaError("config", "ARKCLI_COMMAND 为空", -1)
        exe = _resolve_arkcli()
        if exe is None:
            raise QuotaError(
                "config",
                "未找到 arkcli：请安装（npm i -g @volcengine/ark-cli）"
                "或在 config.py 的 ARKCLI_COMMAND 填写完整路径",
                -1)
        command[0] = exe
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(5, int(config.ARKCLI_TIMEOUT_SEC)),
                creationflags=creationflags,
            )
        except FileNotFoundError as exc:
            raise QuotaError("config",
                             f"未找到 {command[0]}：请先安装 arkcli（npm i -g @volcengine/ark-cli）",
                             -1) from exc
        except subprocess.TimeoutExpired as exc:
            raise QuotaError("network",
                             f"arkcli 超过 {config.ARKCLI_TIMEOUT_SEC} 秒未返回") from exc
        except OSError as exc:
            raise QuotaError("cli", f"arkcli 启动失败：{exc}", -1) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            message = detail[:300] or f"退出码 {proc.returncode}"
            lower = message.lower()
            if any(k in lower for k in ("login", "auth", "登录", "认证", "credential", "sso")):
                # 不把 arkcli 那句英文原文丢给用户（里面还有一条命令），只说要做什么；
                # 原文留在日志里，排查时看得到。
                log.warning("arkcli 认证失败：%s", message)
                raise QuotaError("auth",
                                 "arkcli 未登录或登录已过期（右键桌宠 →「连接额度 / 登录」）",
                                 -1)
            raise QuotaError("cli", f"arkcli 执行失败：{message}")

        payload = _extract_json(proc.stdout)
        snapshot = parse_arkcli_payload(payload, config.QUOTA_PRODUCT)
        self._verify_provisioned(snapshot)
        return snapshot

    def _verify_provisioned(self, snapshot: QuotaSnapshot) -> None:
        """arkcli 对未开通的 agent-plan 也会返回 subscribed:true + 全 0。

        这种情况下用 `arkcli plans get` 复核一次，避免把"根本没开通"
        显示成"已用 0% / 剩余 100%"；复核失败时不做判断（保持原样）。
        """
        if not _looks_unprovisioned(snapshot):
            return
        has_plan = self._arkcli_has_plan()
        if has_plan is False:
            raise QuotaError(
                "not_subscribed",
                "火山方舟账号没有已开通的 Agent Plan / Coding Plan"
                "（额度全部为 0，`arkcli plans get` 为空），"
                "请先到火山方舟控制台订阅后再点「立即刷新」",
                -1)
        if has_plan is True:
            log.info("套餐已开通，当前所有窗口都还是 0%（按真实用量显示）")

    def _arkcli_has_plan(self) -> bool | None:
        """`arkcli plans get` 是否返回至少一个套餐；None = 无法判断。结果缓存 10 分钟。"""
        now = time.monotonic()
        with self._lock:
            if self._plan_check_at and now - self._plan_check_at < PLAN_CHECK_TTL_SEC:
                return self._plan_check_ok
        result: bool | None = None
        exe = _resolve_arkcli()
        if exe is not None:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            try:
                proc = subprocess.run(
                    [exe, "plans", "get", "--format", "json"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(5, int(config.ARKCLI_TIMEOUT_SEC)),
                    creationflags=creationflags,
                )
                if proc.returncode == 0:
                    payload = _extract_json(proc.stdout)
                    plans = payload.get("plans") if isinstance(payload, dict) else None
                    if isinstance(plans, list):
                        result = bool(plans)
                        log.info("arkcli plans get：%d 个已开通套餐", len(plans))
                else:
                    log.warning("arkcli plans get 退出码 %s，跳过未开通判定", proc.returncode)
            except (OSError, subprocess.SubprocessError, QuotaError) as exc:
                log.warning("arkcli plans get 失败，跳过未开通判定：%s", exc)
        with self._lock:
            self._plan_check_at = now
            self._plan_check_ok = result
        return result

    def _fetch_api(self) -> QuotaSnapshot:
        product = str(config.QUOTA_PRODUCT or "coding-plan").strip().lower()
        if product.endswith("-team"):
            # 团队版按 seat 查（GetSeatAFPUsage 等），需要 --seat，暂不支持直连
            raise QuotaError(
                "config",
                "火山 AK/SK 直连不支持团队版（需要 seat id）；请改用 arkcli"
                "（QUOTA_SOURCE=\"arkcli\" 或 \"auto\"）",
                -1,
            )
        if product not in ("coding-plan", "agent-plan"):
            raise QuotaError(
                "config",
                f"未知的 QUOTA_PRODUCT：{product}（可用：coding-plan / agent-plan）",
                -1)
        ak, sk = volc_credentials()
        if not ak or not sk:
            raise QuotaError("config",
                             "未配置火山 AK/SK（config.py 或环境变量 VOLC_ACCESS_KEY / VOLC_SECRET_KEY）",
                             -1)
        region = str(config.VOLC_REGION)
        if product == "agent-plan":
            payload = fetch_afp_usage(ak, sk, region, self._timeout)
            return parse_afp_payload(payload, source="api", source_label="AK/SK")
        payload = fetch_coding_plan_api(ak, sk, region, self._timeout)
        return parse_coding_plan_payload(payload)

    def _fetch_http(self) -> QuotaSnapshot:
        url = str(HTTP_QUOTA_URL or config.QUOTA_HTTP_URL or "").strip()
        if not url:
            raise QuotaError("config", "未配置 QUOTA_HTTP_URL", -1)
        headers = {"Accept": "application/json"}
        token = str(config.QUOTA_HTTP_TOKEN or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if isinstance(config.QUOTA_HTTP_HEADERS, dict):
            headers.update({str(k): str(v) for k, v in config.QUOTA_HTTP_HEADERS.items()})
        try:
            resp = requests.get(url, headers=headers, timeout=self._timeout)
        except requests.exceptions.RequestException as exc:
            detail = str(exc).strip() or type(exc).__name__
            raise QuotaError("network", f"网络异常：{detail}") from exc

        if resp.status_code in (401, 403):
            raise QuotaError("auth", f"额度接口鉴权失败（HTTP {resp.status_code}）", -1)
        if resp.status_code != 200:
            body = (resp.text or resp.reason or "")[:200]
            raise QuotaError("http", f"HTTP {resp.status_code}：{body}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise QuotaError("parse", "额度接口返回的不是 JSON") from exc
        return parse_flat_payload(payload, "http", "自定义接口")

    def _check_low_quota(self, snapshot: QuotaSnapshot) -> None:
        # 演示数据不主动进入低额度状态；但真实低额度切换回演示数据时，
        # 仍需发出解除信号，否则气泡红色警示和宠物角标会一直保留。
        low = False if snapshot.demo else is_low_quota(snapshot)
        if low == self._low:
            return
        self._low = low
        log.info("低额度状态变化：%s（剩余 %s）",
                 "进入" if low else "解除", format_percent(snapshot.remaining_percent))
        self.low_quota.emit(low)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            log.info("状态切换 → %s", state)
            self.state_changed.emit(state)
