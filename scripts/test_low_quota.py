# -*- coding: utf-8 -*-
"""低额度提醒链路自检：跨越阈值 → 宠物角标 + 气泡红色 + 托盘通知。

用法：
    .venv\\Scripts\\python.exe scripts\\test_low_quota.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import main
import quota
from ui import BubbleWindow, PetWindow


class FakeTray:
    def __init__(self):
        self.calls = []

    def notify(self, title, message, msec=6000):
        self.calls.append((title, message, msec))


app = QApplication(sys.argv)
pet = PetWindow()
bubble = BubbleWindow(pet)
tray = FakeTray()

print("阈值 =", quota.config.LOW_QUOTA_REMAINING_PERCENT)
print("低于阈值?", quota.is_low_quota.__name__, "-> 剩余 7%：", end=" ")
from datetime import datetime, timedelta
now = datetime.now(quota.CN_TZ)
snap = quota.parse_flat_payload({
    "percent": 93.0, "used": 93, "total": 100, "unit": "%",
    "reset_at": (now + timedelta(hours=2)).isoformat(),
    "product": "coding-plan", "tier": "pro", "demo": False,
}, "file", "SSO")
print("剩余", round(snap.remaining_percent, 1), "→ 低于阈值:", quota.is_low_quota(snap))

main.on_low_quota(True, pet, bubble, tray)
print("进入低额度 → 宠物角标:", pet._low_quota, "气泡红色:", bubble._low)
for c in tray.calls:
    print("通知：", c)

main.on_low_quota(False, pet, bubble, tray)
print("解除 → 宠物角标:", pet._low_quota, "气泡红色:", bubble._low, "通知条数:", len(tray.calls))
