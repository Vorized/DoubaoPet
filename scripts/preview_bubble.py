# -*- coding: utf-8 -*-
"""开发预览：显示桌宠 + 指定额度气泡，并保存整屏截图。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\preview_bubble.py --plan agent --used 62
    .venv\\Scripts\\python.exe scripts\\preview_bubble.py --plan coding --used 96.5 --low
    .venv\\Scripts\\python.exe scripts\\preview_bubble.py --used 37.4 --demo
截图保存到 %TEMP%\\opencode\\doubao_preview.png
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
import quota  # noqa: E402
from ui import BubbleWindow, PetWindow  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--used", type=float, default=37.4, help="主窗口已用百分比")
    parser.add_argument("--plan", choices=["flat", "coding", "agent"], default="flat",
                        help="flat=扁平 JSON（旧布局）；coding=arkcli 三窗口；agent=GetAFPUsage")
    parser.add_argument("--low", action="store_true", help="低额度红色状态")
    parser.add_argument("--demo", action="store_true", help="演示数据标记")
    parser.add_argument("--delta", default="", help="变化行文字")
    parser.add_argument("--egg", default="",
                        help="点击彩蛋文案（用 \\n 表示换行）：单行走卡片骨架，多行走整颗气泡")
    parser.add_argument("--out", default="", help="截图输出路径")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    pet = PetWindow()
    pet.move(420, 420)
    pet._anchor = pet.pos()  # 预览时直接摆位置：同步漂浮锚点，避免被动画拉回 0,0
    pet.show()
    bubble = BubbleWindow(pet)

    now = datetime.now(quota.CN_TZ)
    if args.plan == "agent":
        # 真实 GetAFPUsage 结构：5h / 周 / 月，带 Quota / Used / ResetTime（毫秒）
        snap = quota.parse_afp_payload({"Result": {
            "PlanType": "medium",
            "AFPFiveHour": {"Quota": 10000, "Used": args.used * 100,
                            "ResetTime": int((now + timedelta(hours=3)).timestamp() * 1000)},
            "AFPWeekly": {"Quota": 35000, "Used": args.used * 350 * 0.4,
                          "ResetTime": int((now + timedelta(days=3)).timestamp() * 1000)},
            "AFPMonthly": {"Quota": 100000, "Used": args.used * 1000 * 0.15,
                           "ResetTime": int((now + timedelta(days=18)).timestamp() * 1000)},
        }})
    elif args.plan == "coding":
        # arkcli 结构：session（最紧）/ weekly / monthly
        snap = quota.parse_arkcli_payload({
            "viewer": {"auth_method": "sso"},
            "items": [{"product": "coding-plan", "edition": "personal",
                       "subscribed": True,
                       "periods": [
                           {"label": "session", "percent": args.used,
                            "reset_at": (now + timedelta(hours=2)).isoformat()},
                           {"label": "weekly", "percent": max(0.0, args.used * 0.4)},
                           {"label": "monthly", "percent": max(0.0, args.used * 0.15)},
                       ]}],
        }, "coding-plan")
    else:
        payload = {
            "percent": args.used,
            "used": round(args.used * 2),  # 演示：总量按 200 计算，仅用于预览排版
            "total": 200,
            "unit": "次",
            "reset_at": (now + timedelta(days=12)).isoformat(),
            "product": "coding-plan",
            "tier": "pro",
            "demo": args.demo,
        }
        snap = quota.parse_flat_payload(payload, "file", "本地文件")
    bubble.show_snapshot(snap)
    bubble.set_low(args.low)
    pet.set_low_quota(args.low)
    if args.delta:
        bubble._delta_text = args.delta
        bubble._delta_color = ui_color(args.low)
    if args.egg:
        bubble._egg = True
        bubble._egg_text = args.egg.replace("\\n", "\n")
    bubble.present()

    out = Path(args.out or (Path(tempfile.gettempdir()) / "opencode" / "doubao_preview.png"))
    out.parent.mkdir(parents=True, exist_ok=True)

    def shoot() -> None:
        screen = QGuiApplication.primaryScreen()
        shot = screen.grabWindow(0)
        shot.save(str(out), "PNG")
        print(f"saved {out} ({shot.width()}x{shot.height()})")
        pet.close()
        app.quit()

    QTimer.singleShot(1200, shoot)
    return app.exec()


def ui_color(low: bool) -> str:
    from ui import DELTA_RESET_COLOR
    return "#D0343F" if low else DELTA_RESET_COLOR


if __name__ == "__main__":
    raise SystemExit(main())
