# -*- coding: utf-8 -*-
"""套餐额度解析 / 多窗口 / 变化检测 / 低额度 / 直连限制单元测试。

运行（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\test_quota_logic.py

只测试业务逻辑，不需要启动桌宠窗口、不访问网络。
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import quota  # noqa: E402


def make_snapshot(percent: float, *, demo: bool = False,
                  product: str = "coding-plan",
                  reset_at: datetime | None = None,
                  window_key: str = "",
                  windows: tuple = ()) -> quota.QuotaSnapshot:
    return quota.QuotaSnapshot(
        product=product,
        product_name="Coding Plan",
        tier="pro",
        used_percent=percent,
        fetched_at=datetime.now(quota.CN_TZ),
        source="file",
        source_label="测试",
        used=percent * 200,
        total=20000,
        unit="次",
        reset_at=reset_at,
        demo=demo,
        windows=windows,
        window_key=window_key,
        window_label=quota.WINDOW_LABELS.get(window_key, ""),
    )


# ------------------------------------------------------------------ 真实响应样例

ARKCLI_ALL_ZERO = {
    "viewer": {"auth_method": "sso"},
    "items": [
        {"product": "agent-plan", "edition": "personal", "subscribed": True,
         "periods": [{"label": "5h", "percent": 0},
                     {"label": "weekly", "percent": 0},
                     {"label": "monthly", "percent": 0}]},
        {"product": "coding-plan", "edition": "personal",
         "subscribed": False, "updated_at": 1792755789},
        {"product": "agent-plan-team", "edition": "team", "subscribed": True,
         "error": "no seat bound to caller for AgentPlan team"},
        {"product": "coding-plan-team", "edition": "team", "subscribed": True,
         "error": "no seat bound to caller for CodingPlan team"},
    ],
}

CODING_PLAN_API = {
    "Result": {
        "Status": "Running",
        "UpdateTimestamp": 1782226000,
        "QuotaUsage": [
            {"Level": "session", "Percent": 12.5, "ResetTimestamp": 1782226478},
            {"Level": "weekly", "Percent": 0.0, "ResetTimestamp": 0},
            {"Level": "monthly", "Percent": 3.0, "ResetTimestamp": 0},
        ],
    },
}

AFP_API = {
    "Result": {
        "PlanType": "medium",
        "AFPFiveHour": {"Quota": 10000, "Used": 0, "ResetTime": -1},
        "AFPWeekly": {"Quota": 35000, "Used": 8750, "ResetTime": 1785686400000},
        "AFPMonthly": {"Quota": 100000, "Used": 25000, "ResetTime": 1787846399000},
        "AFPDaily": {"Quota": 50000, "Used": 0, "ResetTime": 1785340800000},
    },
}


class ParseTests(unittest.TestCase):
    def test_extract_json_tolerates_ansi_and_logs(self) -> None:
        text = (
            "\x1b[32mINFO\x1b[0m 正在读取套餐...\n"
            '{"viewer": {"auth_method": "sso"}, "items": []}\n'
            "done\n"
        )
        self.assertEqual(quota._extract_json(text)["viewer"]["auth_method"], "sso")

    def test_parse_flat_percent(self) -> None:
        snap = quota.parse_flat_payload(
            {"percent": "37.4", "used": "7,480", "total": 20000,
             "unit": "次", "product": "coding-plan", "tier": "pro"},
            "file", "本地文件")
        self.assertAlmostEqual(snap.used_percent, 37.4)
        self.assertAlmostEqual(snap.remaining_percent, 62.6)
        self.assertEqual(snap.used, 7480)
        self.assertEqual(snap.total, 20000)
        self.assertEqual(snap.unit, "次")
        self.assertEqual(snap.product_name, "Coding Plan · PRO")
        self.assertEqual(snap.windows, ())   # 扁平数据源没有窗口信息

    def test_parse_flat_used_total(self) -> None:
        snap = quota.parse_flat_payload(
            {"used": 2500, "total": 10000, "unit": "点"}, "http", "自定义接口")
        self.assertAlmostEqual(snap.used_percent, 25.0)
        self.assertEqual(snap.source, "http")
        self.assertEqual(snap.source_label, "自定义接口")

    def test_parse_flat_missing_percent_is_parse_error(self) -> None:
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_flat_payload({"used": 10}, "file", "本地文件")
        self.assertEqual(ctx.exception.kind, "parse")

    def test_parse_flat_bad_reset_at_does_not_fail(self) -> None:
        """时间字段只是展示信息，解析失败不能让整次同步失败。"""
        snap = quota.parse_flat_payload(
            {"percent": 37.4, "reset_at": "-1"}, "file", "本地文件")
        self.assertIsNone(snap.reset_at)
        self.assertAlmostEqual(snap.used_percent, 37.4)

    def test_parse_flat_items_keeps_file_source(self) -> None:
        payload = {"items": [{"product": "agent-plan", "edition": "personal",
                              "subscribed": True,
                              "periods": [{"label": "monthly", "percent": 12}]}]}
        snap = quota.parse_flat_payload(payload, "file", "本地文件")
        self.assertEqual(snap.source, "file")
        self.assertEqual(snap.source_label, "本地文件")
        self.assertAlmostEqual(snap.used_percent, 12.0)

    def test_parse_arkcli_monthly(self) -> None:
        payload = {
            "viewer": {"auth_method": "sso"},
            "items": [
                {"product": "coding-plan", "edition": "personal",
                 "subscribed": False, "error": "not subscribed"},
                {"product": "coding-plan", "edition": "personal", "tier": "pro",
                 "subscribed": True,
                 "periods": [
                     {"label": "daily", "percent": 10},
                     {"label": "monthly", "percent": "37.4",
                      "used": "7,480", "total": 20000,
                      "reset_at": "2026-11-01T00:00:00+08:00"},
                 ],
                 "updated_at": "2026-10-20T10:00:00+08:00",
                 "status": "active"},
            ],
        }
        snap = quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual(snap.product, "coding-plan")
        self.assertEqual(snap.source, "arkcli")
        self.assertEqual(snap.source_label, "SSO")
        self.assertAlmostEqual(snap.used_percent, 37.4)
        self.assertEqual(snap.used, 7480)
        self.assertEqual(snap.window_key, "monthly")
        self.assertEqual(snap.window_label, "月")
        self.assertEqual(snap.reset_at.strftime("%Y-%m-%d"), "2026-11-01")

    def test_parse_arkcli_falls_back_to_agent_plan(self) -> None:
        payload = {
            "items": [{
                "product": "agent-plan", "tier": "small", "subscribed": True,
                "periods": [{"label": "monthly", "percent": 12.5,
                             "used": 125, "total": 1000}],
            }],
        }
        snap = quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual(snap.product, "agent-plan")
        self.assertEqual(snap.unit, "点")
        self.assertEqual(snap.product_name, "Agent Plan · small")

    def test_parse_arkcli_empty_items_is_not_subscribed(self) -> None:
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_arkcli_payload({"items": []}, "coding-plan")
        self.assertEqual(ctx.exception.kind, "not_subscribed")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_parse_arkcli_not_logged_in(self) -> None:
        payload = {"viewer": {"auth_method": "none"}, "items": []}
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_parse_arkcli_missing_subscribed_field(self) -> None:
        payload = {"items": [{
            "product": "coding-plan",
            "periods": [{"label": "monthly", "percent": 20}],
        }]}
        snap = quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertAlmostEqual(snap.used_percent, 20.0)

    def test_parse_api_payload(self) -> None:
        payload = {
            "ResponseMetadata": {},
            "Result": {
                "QuotaUsage": [{
                    "Level": "monthly",
                    "Percent": 42.5,
                    "ResetTimestamp": "2026-11-01T00:00:00+08:00",
                }],
                "UpdateTimestamp": "2026-10-20T10:00:00+08:00",
                "Status": "Normal",
            },
        }
        snap = quota.parse_coding_plan_payload(payload)
        self.assertAlmostEqual(snap.used_percent, 42.5)
        self.assertEqual(snap.source, "api")
        self.assertEqual(snap.source_label, "AK/SK")
        self.assertEqual(snap.status, "Normal")

    def test_parse_api_auth_error_stops_retry(self) -> None:
        payload = {
            "ResponseMetadata": {
                "Error": {"Code": "InvalidCredential", "Message": "bad signature"}
            }
        }
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_coding_plan_payload(payload)
        self.assertEqual(ctx.exception.kind, "auth")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_datetime_timestamp_and_iso(self) -> None:
        ms = quota._as_datetime(1767225600000)
        self.assertEqual(ms.strftime("%Y-%m-%d %H:%M"), "2026-01-01 08:00")
        iso = quota._as_datetime("2026-01-01T00:00:00Z")
        self.assertEqual(iso.strftime("%Y-%m-%d %H:%M"), "2026-01-01 08:00")

    def test_remaining_never_negative(self) -> None:
        self.assertEqual(make_snapshot(103.2).remaining_percent, 0.0)


class WindowTests(unittest.TestCase):
    """多窗口：主数字取最紧窗口（打平取更短的窗口）。"""

    def test_arkcli_reads_all_windows_and_picks_tightest(self) -> None:
        payload = {"viewer": {"auth_method": "sso"}, "items": [{
            "product": "agent-plan", "edition": "personal", "subscribed": True,
            "periods": [
                {"label": "5h", "percent": 62, "used": 620, "total": 1000},
                {"label": "weekly", "percent": 20, "used": 700, "total": 3500},
                {"label": "monthly", "percent": 5, "used": 5000, "total": 100000},
            ]}]}
        snap = quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual([w.key for w in snap.windows], ["5h", "weekly", "monthly"])
        self.assertEqual(snap.window_key, "5h")
        self.assertEqual(snap.window_label, "5 小时")
        self.assertAlmostEqual(snap.used_percent, 62.0)
        self.assertAlmostEqual(snap.used, 620.0)
        self.assertAlmostEqual(snap.total, 1000.0)

    def test_window_tie_prefers_shorter_window(self) -> None:
        windows = [
            quota.QuotaWindow(key="monthly", used_percent=25.0),
            quota.QuotaWindow(key="weekly", used_percent=25.0),
        ]
        self.assertEqual(quota._select_primary(windows).key, "weekly")
        windows.append(quota.QuotaWindow(key="5h", used_percent=25.0))
        self.assertEqual(quota._select_primary(windows).key, "5h")

    def test_daily_window_is_skipped(self) -> None:
        payload = {"viewer": {"auth_method": "sso"}, "items": [{
            "product": "agent-plan", "subscribed": True,
            "periods": [{"label": "daily", "percent": 99},
                        {"label": "monthly", "percent": 5}]}]}
        snap = quota.parse_arkcli_payload(payload)
        self.assertEqual([w.key for w in snap.windows], ["monthly"])
        self.assertAlmostEqual(snap.used_percent, 5.0)

    def test_coding_plan_api_all_windows(self) -> None:
        snap = quota.parse_coding_plan_payload(CODING_PLAN_API)
        self.assertEqual([w.key for w in snap.windows],
                         ["session", "weekly", "monthly"])
        self.assertEqual(snap.window_key, "session")   # 12.5% 最紧
        self.assertAlmostEqual(snap.used_percent, 12.5)
        self.assertEqual(snap.window_label, "会话")
        self.assertIsNotNone(snap.windows[0].reset_at)
        self.assertIsNone(snap.windows[1].reset_at)    # ResetTimestamp=0 → 无重置

    def test_coding_plan_reclaimed_is_not_subscribed(self) -> None:
        payload = {"Result": {"Status": "Reclaimed", "UpdateTimestamp": 1785322689}}
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_coding_plan_payload(payload)
        self.assertEqual(ctx.exception.kind, "not_subscribed")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_coding_plan_empty_quota_without_status_is_retryable(self) -> None:
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_coding_plan_payload({"Result": {}})
        self.assertEqual(ctx.exception.kind, "parse")
        self.assertIsNone(ctx.exception.retry_after)

    def test_afp_windows_and_absolute_usage(self) -> None:
        snap = quota.parse_afp_payload(AFP_API)
        self.assertEqual(snap.product, "agent-plan")
        self.assertEqual(snap.tier, "medium")
        self.assertEqual(snap.product_name, "Agent Plan · medium")
        self.assertEqual([w.key for w in snap.windows], ["5h", "weekly", "monthly"])
        # 5h 是 0%、周与月都是 25% → 打平取更短的窗口（周）
        self.assertEqual(snap.window_key, "weekly")
        self.assertAlmostEqual(snap.used_percent, 25.0)
        self.assertAlmostEqual(snap.used, 8750.0)
        self.assertAlmostEqual(snap.total, 35000.0)
        self.assertEqual(snap.unit, "点")
        self.assertEqual(snap.reset_at.strftime("%Y-%m-%d %H:%M"), "2026-08-03 00:00")

    def test_afp_zero_quota_is_not_subscribed(self) -> None:
        payload = {"Result": {"PlanType": "",
                              "AFPMonthly": {"Quota": 0, "Used": 0, "ResetTime": 0}}}
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_afp_payload(payload)
        self.assertEqual(ctx.exception.kind, "not_subscribed")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_afp_null_result_is_parse_error(self) -> None:
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_afp_payload({"Result": None})
        self.assertEqual(ctx.exception.kind, "parse")

    def test_looks_unprovisioned(self) -> None:
        all_zero = quota.parse_arkcli_payload(ARKCLI_ALL_ZERO, "coding-plan")
        self.assertTrue(quota._looks_unprovisioned(all_zero))
        used = quota.parse_afp_payload(AFP_API)
        self.assertFalse(quota._looks_unprovisioned(used))


class ErrorClassificationTests(unittest.TestCase):
    def test_arkcli_network_error_is_retryable(self) -> None:
        payload = {"viewer": {"auth_method": "sso"}, "items": [{
            "product": "agent-plan", "edition": "personal",
            "error": 'usage.get_afp_usage: ark: GetAFPUsage: Post '
                     '"https://ark.cn-beijing.volcengineapi.com/?Action=GetAFPUsage": '
                     'dial tcp 10.0.0.1:443: i/o timeout'}]}
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual(ctx.exception.kind, "network")
        self.assertIsNone(ctx.exception.retry_after)   # None = 继续退避重试

    def test_arkcli_no_seat_error_is_permanent(self) -> None:
        payload = {"viewer": {"auth_method": "sso"}, "items": [{
            "product": "coding-plan-team", "edition": "team", "subscribed": True,
            "error": "no seat bound to caller for CodingPlan team"}]}
        with self.assertRaises(quota.QuotaError) as ctx:
            quota.parse_arkcli_payload(payload, "coding-plan")
        self.assertEqual(ctx.exception.kind, "not_subscribed")
        self.assertEqual(ctx.exception.retry_after, -1)


class DiffTests(unittest.TestCase):
    def test_usage_increase(self) -> None:
        change = quota.diff_snapshots(make_snapshot(37.4), make_snapshot(40.0))
        self.assertIsNotNone(change)
        self.assertEqual(change.kind, "use")
        self.assertAlmostEqual(change.delta_percent, 2.6)

    def test_usage_adjust_down(self) -> None:
        change = quota.diff_snapshots(make_snapshot(40.0), make_snapshot(38.0))
        self.assertIsNotNone(change)
        self.assertEqual(change.kind, "adjust")

    def test_period_reset_by_timestamp(self) -> None:
        prev = make_snapshot(96.0, reset_at=datetime(2026, 11, 1, tzinfo=quota.CN_TZ))
        cur = make_snapshot(2.0, reset_at=datetime(2026, 12, 1, tzinfo=quota.CN_TZ))
        change = quota.diff_snapshots(prev, cur)
        self.assertIsNotNone(change)
        self.assertEqual(change.kind, "reset")

    def test_period_reset_fallback(self) -> None:
        change = quota.diff_snapshots(make_snapshot(96.0), make_snapshot(0.5))
        self.assertIsNotNone(change)
        self.assertEqual(change.kind, "reset")

    def test_no_change_for_tiny_delta(self) -> None:
        self.assertIsNone(quota.diff_snapshots(make_snapshot(37.4), make_snapshot(37.42)))

    def test_demo_or_product_switch_is_ignored(self) -> None:
        self.assertIsNone(quota.diff_snapshots(
            make_snapshot(37.4), make_snapshot(60.0, demo=True)))
        self.assertIsNone(quota.diff_snapshots(
            make_snapshot(37.4), make_snapshot(60.0, product="agent-plan")))

    def test_window_switch_is_ignored(self) -> None:
        """主窗口从 5h 换成月（例如 5h 重置后不再是最高），数值不可比。"""
        prev = make_snapshot(60.0, window_key="5h",
                             windows=(quota.QuotaWindow("5h", 60.0),))
        cur = make_snapshot(30.0, window_key="weekly",
                            windows=(quota.QuotaWindow("weekly", 30.0),))
        self.assertIsNone(quota.diff_snapshots(prev, cur))

    def test_same_window_delta_is_reported(self) -> None:
        prev = make_snapshot(20.0, window_key="5h")
        cur = make_snapshot(26.0, window_key="5h")
        change = quota.diff_snapshots(prev, cur)
        self.assertIsNotNone(change)
        self.assertIn("5 小时窗口", change.message)


class LowQuotaTests(unittest.TestCase):
    def test_is_low_quota_threshold(self) -> None:
        with mock.patch.object(config, "LOW_QUOTA_WARN_ENABLED", True), \
                mock.patch.object(config, "LOW_QUOTA_REMAINING_PERCENT", 10.0):
            self.assertTrue(quota.is_low_quota(make_snapshot(96.5)))
            self.assertFalse(quota.is_low_quota(make_snapshot(89.9)))

    def test_low_quota_disabled(self) -> None:
        with mock.patch.object(config, "LOW_QUOTA_WARN_ENABLED", False):
            self.assertFalse(quota.is_low_quota(make_snapshot(99.0)))

    def test_service_clears_low_state_when_switching_to_demo(self) -> None:
        """回归：真实低额度切到演示数据时，必须发出解除信号清掉红色警示。"""
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
        svc = quota.QuotaService()
        seen: list[bool] = []
        svc.low_quota.connect(seen.append)

        svc._check_low_quota(make_snapshot(96.5))        # 真实低额度 → 进入
        svc._check_low_quota(make_snapshot(37.4, demo=True))  # 演示数据 → 解除
        svc._check_low_quota(make_snapshot(99.0, demo=True))  # 演示数据不触发

        self.assertEqual(seen, [True, False])


class ApiSourceTests(unittest.TestCase):
    def _service(self) -> quota.QuotaService:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
        return quota.QuotaService()

    def test_direct_api_requires_credentials(self) -> None:
        svc = self._service()
        with mock.patch.object(config, "QUOTA_PRODUCT", "coding-plan"), \
                mock.patch.object(quota, "volc_credentials", return_value=("", "")):
            with self.assertRaises(quota.QuotaError) as ctx:
                svc._fetch_api()
        self.assertEqual(ctx.exception.kind, "config")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_agent_plan_direct_api_requires_credentials(self) -> None:
        """Agent Plan 现在也支持 AK/SK 直连，但同样需要凭据。"""
        svc = self._service()
        with mock.patch.object(config, "QUOTA_PRODUCT", "agent-plan"), \
                mock.patch.object(quota, "volc_credentials", return_value=("", "")):
            with self.assertRaises(quota.QuotaError) as ctx:
                svc._fetch_api()
        self.assertEqual(ctx.exception.kind, "config")
        self.assertEqual(ctx.exception.retry_after, -1)

    def test_direct_api_rejects_team_product(self) -> None:
        svc = self._service()
        with mock.patch.object(config, "QUOTA_PRODUCT", "coding-plan-team"), \
                mock.patch.object(quota, "volc_credentials", return_value=("ak", "sk")):
            with self.assertRaises(quota.QuotaError) as ctx:
                svc._fetch_api()
        self.assertEqual(ctx.exception.kind, "config")
        self.assertEqual(ctx.exception.retry_after, -1)


class ServiceBehaviourTests(unittest.TestCase):
    def _service(self) -> quota.QuotaService:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841
        return quota.QuotaService()

    def test_refresh_merged_while_fetching(self) -> None:
        """抓取进行中的悬停 / 点击不应再叠加一次抓取（历史 bug：每次都多跑一次）。"""
        svc = self._service()
        svc._fetching = True
        svc.refresh_now()
        self.assertFalse(svc._wake.is_set())

    def test_refresh_wakes_when_idle(self) -> None:
        svc = self._service()
        svc._fetching = False
        svc._state = "backoff"
        svc.refresh_now()
        self.assertTrue(svc._wake.is_set())

    def test_refresh_debounced_after_success(self) -> None:
        import time

        svc = self._service()
        svc._state = "ok"
        svc._last_ok_at = time.monotonic()
        svc.refresh_now()
        self.assertFalse(svc._wake.is_set())


class DisplayTests(unittest.TestCase):
    def test_quota_title_includes_plan_and_window(self) -> None:
        from ui import _quota_title

        self.assertEqual(_quota_title("Agent Plan", "5 小时", True, False),
                         "Agent Plan · 5 小时窗口剩余额度")
        self.assertEqual(_quota_title("Coding Plan · PRO", "月", True, False),
                         "Coding Plan · 本月剩余额度")
        self.assertEqual(_quota_title("Coding Plan", "周", False, False),
                         "Coding Plan · 周窗口已用额度")
        self.assertIn("演示", _quota_title("Agent Plan", "5 小时", True, True))

    def test_format_snapshot_lists_windows(self) -> None:
        snap = quota.parse_afp_payload(AFP_API)
        text = quota.format_snapshot(snap)
        self.assertIn("周窗口", text)
        self.assertIn("8,750 / 35,000 点", text)
        self.assertIn("5h 0%", text)
        self.assertIn("月 25%", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
