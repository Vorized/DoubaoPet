# -*- coding: utf-8 -*-
"""快速修改本地额度文件 quota.json（演示 / 调试用）。

示例（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\set_quota.py 37.4
    .venv\\Scripts\\python.exe scripts\\set_quota.py 96.5 --no-demo
    .venv\\Scripts\\python.exe scripts\\set_quota.py --used 19300 --total 20000 --unit 次
    .venv\\Scripts\\python.exe scripts\\set_quota.py 8 --reset-days 3

只写本地文件，不影响真实 arkcli / API 数据源。桌宠会在下一次轮询
（默认 60 秒内）或鼠标悬停 / 点击时读取新值。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import quota  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="修改 quota.json 中的额度（百分比 / 已用 / 总量）。")
    parser.add_argument("percent", nargs="?", type=float,
                        help="已用百分比，例如 37.4；省略时用 --used/--total 计算")
    parser.add_argument("--used", type=float, help="已用数量")
    parser.add_argument("--total", type=float, help="额度总量")
    parser.add_argument("--unit", help="单位，例如 次 / 点 / 百分号")
    parser.add_argument("--product", help="套餐标识，例如 coding-plan / agent-plan")
    parser.add_argument("--tier", help="套餐档位，例如 lite / pro")
    parser.add_argument("--label", help="数据来源说明，默认「本地文件」")
    parser.add_argument("--reset-days", type=float,
                        help="距离下次重置的天数（可写小数）")
    parser.add_argument("--reset-at", help="重置时间，ISO 格式，例如 2026-11-01T00:00:00+08:00")
    parser.add_argument("--demo", action=argparse.BooleanOptionalAction, default=None,
                        help="是否标记为演示数据（默认保留原值；新文件为否）")
    parser.add_argument("--path", help="quota.json 路径（默认读取 config.QUOTA_FILE）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.path) if args.path else quota.quota_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError) as exc:
            print(f"读取原文件失败，将重新创建：{exc}", file=sys.stderr)

    total = args.total if args.total is not None else data.get("total")
    used = args.used
    percent = args.percent

    if percent is None and used is not None and total:
        percent = used / float(total) * 100.0
    if percent is None:
        print("请提供百分比（37.4）或同时提供 --used 与 --total。", file=sys.stderr)
        return 2
    if percent < 0:
        print("百分比不能为负数。", file=sys.stderr)
        return 2

    if used is None and total not in (None, 0):
        used = round(percent / 100.0 * float(total), 2)

    data["percent"] = round(float(percent), 2)
    if used is not None:
        data["used"] = used
    else:
        data.pop("used", None)
    if total is not None:
        data["total"] = total
    else:
        data.pop("total", None)

    data["unit"] = args.unit if args.unit is not None else data.get("unit", "次")
    data["product"] = args.product or data.get("product", "coding-plan")
    data["tier"] = args.tier if args.tier is not None else data.get("tier", "pro")

    demo = bool(args.demo) if args.demo is not None else bool(data.get("demo", False))
    if args.label:
        label = args.label
    else:
        label = str(data.get("source_label") or "")
        if not label or (not demo and label == "演示数据"):
            label = "本地文件"
    data["demo"] = demo
    data["source_label"] = label

    if args.reset_at:
        data["reset_at"] = args.reset_at
    elif args.reset_days is not None:
        reset = datetime.now(quota.CN_TZ) + timedelta(days=args.reset_days)
        data["reset_at"] = reset.isoformat(timespec="seconds")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")

    state = "演示数据" if demo else "真实数据"
    print(f"已写入 {path}")
    print(f"  已用 {data['percent']:g}% · {state} · 来源 {label}")
    if data.get("used") is not None and data.get("total"):
        print(f"  {data['used']:g} / {data['total']:g} {data['unit']}"
              f"（剩余 {max(0.0, 100 - data['percent']):g}%）")
    if demo:
        print("  提示：demo=true 不会触发低额度警示；要测试请加 --no-demo。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
