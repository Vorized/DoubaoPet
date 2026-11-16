# -*- coding: utf-8 -*-
"""气泡卡片的功能自检：尺寸、朝向、状态、配置预览、按键气泡。

用法：
    .venv\\Scripts\\python.exe scripts\\test_bubble_ui.py
"""
import os, sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
import quota
from ui import BubbleWindow

class FakePet:
    _dpr = 1.0; _content_top = 0; _content_bottom = 305
    def __init__(self, w=243): self._pet_w = w; self._mirrored = False
    def attach_bubble(self, b): self.bubble = b
    def sync_mirror(self): pass
    def is_mirrored(self): return self._mirrored
    def x(self): return 100
    def y(self): return 100
    def width(self): return self._pet_w
    def pos(self):
        from PySide6.QtCore import QPoint
        return QPoint(100, 100)
    def height(self): return 305
    def screen(self): return QApplication.primaryScreen()

def snap(used):
    now = datetime.now(quota.CN_TZ)
    return quota.parse_flat_payload({"percent": used, "used": round(used * 2), "total": 200,
        "unit": "次", "reset_at": (now + timedelta(hours=3)).isoformat(),
        "product": "coding-plan", "tier": "pro", "demo": False}, "file", "本地文件")

app = QApplication(sys.argv)
ok = True
def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond: ok = False

b = BubbleWindow(FakePet(243))
m = b.layout_metrics()
check("默认宽度 = 宠物宽度", m["body_w"] == 243, str(m))
check("卡片高度比例 0.64", abs(m["body_h"] - round(243 * 0.64)) <= 1, str(m["body_h"]))
check("窗口高 = 卡片 + 尾迹", m["win_h"] == m["body_h"] + m["dots_h"], str(m))
check("尾迹在 0.42 处", abs(b.tail_anchor_x(False) - 243 * 0.42) < 12, f"{b.tail_anchor_x(False):.1f}")
check("镜像尾迹对称", abs((b.tail_anchor_x(False) + b.tail_anchor_x(True)) - 243) < 1,
      f"{b.tail_anchor_x(True):.1f}")

b.show_snapshot(snap(37.4))
pm = b.grab()
check("正常态可渲染", pm.width() > 200 and pm.height() > 100, f"{pm.width()}x{pm.height()}")

b.set_low(True)
b.set_preview_state(False, False)
pm_low = b.grab()
check("低额度可渲染", not pm_low.isNull())
b.set_low(False)

b.set_preview_state(True, False)
check("水平镜像可用", b._flip_x is True)
b.set_preview_state(True, True)
check("下翻可用", b._flip is True)
pm_flip = b.grab()
check("下翻可渲染", not pm_flip.isNull())
b.set_preview_state(False, False)

def has_color(pm, hex_color, tol=10, min_hits=1):
    """判断截图像素里是否出现某个颜色（用来区分「有卡片骨架 / 没有」）。

    圆角描边的抗锯齿也会落在底槽色附近，所以按命中数量判定。
    """
    img = pm.toImage().convertToFormat(QImage.Format_RGB32)
    r0 = int(hex_color[1:3], 16)
    g0 = int(hex_color[3:5], 16)
    b0 = int(hex_color[5:7], 16)
    hits = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if (abs(c.red() - r0) <= tol and abs(c.green() - g0) <= tol
                    and abs(c.blue() - b0) <= tol):
                hits += 1
                if hits >= min_hits:
                    return True
    return False


# 点击彩蛋：单行走卡片骨架（标题 / 标签 / 底槽 / 提示），多行保持整颗气泡
b2 = BubbleWindow(FakePet(243))
b2.show_snapshot(snap(37.4))
b2.set_preview_state(False, False)
b2._egg = True
b2._egg_text = "？！豆豆！？"
pm_egg = b2.grab()
check("单行彩蛋可渲染", not pm_egg.isNull(), f"{pm_egg.width()}x{pm_egg.height()}")
check("单行彩蛋保留卡片骨架", has_color(pm_egg, "#E9EDF9", min_hits=40))

b2.set_low(True)
pm_egg_low = b2.grab()
check("低额度彩蛋文字不变红", not has_color(pm_egg_low, "#D0343F", tol=18))
b2.set_low(False)

b2._egg_text = "我用最直白最不绕弯子的方式告诉你：\n我不知道"
pm_egg2 = b2.grab()
check("多行彩蛋可渲染", not pm_egg2.isNull())
check("多行彩蛋不画卡片骨架", not has_color(pm_egg2, "#E9EDF9", min_hits=40))

b3 = BubbleWindow(FakePet(243))
b3.show_error("auth", "登录已过期，请重新执行 arkcli login")
b3._egg = True
b3._egg_text = "好的。"
pm_egg_err = b3.grab()
check("无额度数据时彩蛋仍走卡片", has_color(pm_egg_err, "#E9EDF9", min_hits=40))

b2._egg = False
b2._egg_text = ""

b.show_keycast(["Ctrl", "Shift", "K"])
pm_key = b.grab()
check("按键气泡可渲染", not pm_key.isNull(), f"{b._body_w}x{b._body_h}")
b.clear_keycast() if hasattr(b, "clear_keycast") else None

b.reload_layout()
check("reload 后尺寸不变", b.layout_metrics()["body_w"] == 243)

# 配置预览窗口
from config_preview import ConfigPreviewDialog
d = ConfigPreviewDialog()
d.resize(1180, 860)
d.layout().activate()
d._spins["PET_SCALE"].setValue(0.28)
d._percent.setValue(75.0)
d._refresh()
pm2 = d.grab()
check("配置预览可渲染", pm2.width() > 1000, f"{pm2.width()}x{pm2.height()}")
out = Path(os.environ["TEMP"]) / "bubble_config_preview.png"
pm2.save(str(out))
print("saved", out)

print("RESULT", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
