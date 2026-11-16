# -*- coding: utf-8 -*-
"""豆包桌宠 —— 配置预览（数值填写 + 模拟显示效果）。

从右键菜单「配置预览 / 数值调整」打开：

- 左侧把 ``config.py`` 里影响外观的数值做成输入框，右侧按**真实像素**模拟桌面效果；
- 预览里的气泡不是重画的：直接 render 一个隐藏的 ``ui.BubbleWindow``，
  所以字号、行高、进度条、尾迹朝向和真实运行完全一致；
- 拖动预览里的宠物可以模拟「右半边镜像 / 贴屏幕边缘」，气泡偏移会跟着变；
- 改完可以「应用到桌宠」立即看真实效果，或「保存到 config.py」写回文件
  （只替换对应的一行，保留注释；同目录留一份 ``config.py.bak``）。
"""
from __future__ import annotations

import logging
import math
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QDialog, QDoubleSpinBox, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

import config
import quota
from ui import (BREATH_PERIOD_FACTOR, CLICK_SQUASH_H, CLICK_SQUASH_W,
                BubbleWindow)

log = logging.getLogger("pet.config_preview")

# 与 config.py 出厂默认值保持一致（「恢复默认」用）
DEFAULTS: dict[str, float | int] = {
    "PET_SCALE": 0.19,
    "FLOAT_AMPLITUDE_PX": 6,
    "FLOAT_PERIOD_SEC": 2.4,
    "BREATH_SCALE": 0.03,
    "ANIM_FPS": 30,
    "BUBBLE_OFFSET_X_RATIO": -0.5,
    "BUBBLE_MIRROR_OFFSET_X_RATIO": 0.5,
    "BUBBLE_AUTOHIDE_SEC": 6,
    "START_DX": 80,
    "START_DY": 60,
    "LOW_QUOTA_REMAINING_PERCENT": 10.0,
}


@dataclass(frozen=True)
class Field:
    """一个可填写的配置项。"""

    key: str
    label: str
    minimum: float
    maximum: float
    step: float
    decimals: int
    hint: str = ""

    @property
    def is_int(self) -> bool:
        return self.decimals == 0


GROUPS: tuple[tuple[str, tuple[Field, ...]], ...] = (
    ("宠物尺寸与动画", (
        Field("PET_SCALE", "缩放比例", 0.06, 0.60, 0.01, 2,
              "0.19 ≈ 243px 宽；气泡字号 / 进度条 / 尾迹间距会等比缩放"),
        Field("FLOAT_AMPLITUDE_PX", "漂浮幅度（px）", 0, 80, 1, 0,
              "上下浮动的高度，0 = 完全不动"),
        Field("FLOAT_PERIOD_SEC", "漂浮周期（秒）", 0.5, 10.0, 0.1, 1,
              "一个完整来回的时间"),
        Field("BREATH_SCALE", "呼吸幅度", 0.0, 0.2, 0.005, 2,
              "0.03 = ±3%，以脚底为锚点缩放"),
        Field("ANIM_FPS", "动画帧率", 1, 60, 1, 0,
              "30 已足够顺滑，调低更省 CPU"),
    )),
    ("气泡定位", (
        Field("BUBBLE_OFFSET_X_RATIO", "水平偏移 · 常规", -1.5, 1.5, 0.02, 2,
              "相对宠物宽度：-0.5 = 向左移半个宠物身宽"),
        Field("BUBBLE_MIRROR_OFFSET_X_RATIO", "水平偏移 · 镜像", -1.5, 1.5, 0.02, 2,
              "宠物在屏幕右半边时，替换上面的值"),
        Field("BUBBLE_AUTOHIDE_SEC", "自动收起（秒）", 1, 600, 1, 0,
              "鼠标停在气泡上时不会收起"),
    )),
    ("启动位置", (
        Field("START_DX", "距边缘 X（px）", 0, 6000, 1, 0, ""),
        Field("START_DY", "距边缘 Y（px）", 0, 6000, 1, 0, ""),
    )),
    ("额度警示", (
        Field("LOW_QUOTA_REMAINING_PERCENT", "低额度阈值（%）", 0, 50, 0.5, 1,
              "模拟额度低于该值时，预览会变红并显示 ! 角标"),
    )),
)

STYLE = """
QDialog#ConfigPreview { background: #F4F6FC; }
QLabel#title { font-size: 19px; font-weight: 700; color: #1E2A4A; }
QLabel#subtitle { font-size: 12px; color: #6B7699; }
QLabel#fieldLabel { font-size: 12px; color: #2C3A7A; }
QLabel#hint { font-size: 11px; color: #8A93B8; }
QLabel#info { font-size: 11px; color: #4B5680; background: #FFFFFF;
              border: 1px solid #E3E8F7; border-radius: 6px; padding: 7px 10px; }
QLabel#status { font-size: 12px; color: #6B7699; }
QGroupBox { background: #FFFFFF; border: 1px solid #E3E8F7; border-radius: 10px;
            margin-top: 12px; padding: 6px 0 2px 0; font-size: 12px;
            font-weight: 600; color: #4D6BFE; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 12px; padding: 0 5px; background: #FFFFFF; }
QDoubleSpinBox, QSpinBox { background: #F8FAFF; border: 1px solid #DCE3F6;
                           border-radius: 6px; padding: 4px 6px; min-width: 92px;
                           color: #1E2A4A; font-size: 12px; }
QDoubleSpinBox:focus, QSpinBox:focus { border-color: #4D6BFE; background: #FFFFFF; }
QPushButton { background: #FFFFFF; border: 1px solid #D8DFF2; border-radius: 7px;
              padding: 7px 16px; color: #2C3A7A; font-size: 12px; }
QPushButton:hover { background: #F2F5FF; border-color: #B9C6EE; }
QPushButton#primary { background: #4D6BFE; border: none; color: #FFFFFF;
                      font-weight: 600; }
QPushButton#primary:hover { background: #3F5BEA; }
QPushButton:disabled { color: #A9B1C9; background: #F2F3F8; border-color: #E5E8F2; }
QScrollArea { background: transparent; border: none; }
QWidget#scrollBody { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #C9D3EC; border-radius: 4px; min-height: 40px; }
QScrollBar::handle:vertical:hover { background: #AEBCE4; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { height: 4px; background: #DCE3F6; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4D6BFE; border-radius: 2px; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -6px 0;
                             border-radius: 7px; background: #FFFFFF;
                             border: 2px solid #4D6BFE; }
"""


def _ui_font(px: int, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(["MiSans", "HarmonyOS Sans SC", "微软雅黑",
                      "Microsoft YaHei UI", "Segoe UI"])
    font.setPixelSize(px)
    font.setBold(bold)
    return font


# ============================ 写回 config.py ============================

def _format_literal(value: object) -> str:
    """把数值格式化成 config.py 里的字面量（整数不带小数点）。"""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return f"{number:.1f}"  # 10.0 而不是 10，和原文件风格一致
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def save_config_values(path: Path, values: dict[str, object]) -> Path:
    """把数值写回 config.py，返回备份文件路径。

    只替换 ``KEY = value`` 这一行，保留行内注释、空行和其余内容；
    写文件前先在原目录生成 ``xxx.py.bak``。
    """
    text = path.read_text(encoding="utf-8")
    missing: list[str] = []
    for key, value in values.items():
        pattern = re.compile(
            rf"^({re.escape(key)}[ \t]*=[ \t]*)([^#\r\n]+?)([ \t]*#[^\r\n]*)?(?=\r?$)",
            re.MULTILINE,
        )
        new_text, count = pattern.subn(
            lambda m, lit=_format_literal(value): f"{m.group(1)}{lit}{m.group(3) or ''}",
            text,
            count=1,
        )
        if count:
            text = new_text
        else:
            missing.append(key)
    if missing:
        raise ValueError("config.py 里没有找到这些配置项：" + "、".join(missing))

    backup = path.with_name(path.name + ".bak")
    if path.exists():
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return backup


# ============================ 预览舞台 ============================

class _PreviewPet:
    """``BubbleWindow`` 需要的宠物尺寸（鸭子类型）。

    预览不创建真实宠物窗口，只提供排版所需的宽度 / dpr；真实窗口仍然由
    ``ui.PetWindow`` 负责，两边用的是同一份气泡绘制代码。
    """

    def __init__(self, stage: "PreviewStage") -> None:
        self._stage = stage
        self._pet_w = stage._pet_w
        self._dpr = stage._dpr
        self._content_top = 0
        self._content_bottom = 0
        self._bubble = None

    def width(self) -> int:
        return self._stage.pet_window_width()

    def height(self) -> int:
        return self._stage.pet_window_height()

    def x(self) -> int:
        return 0

    def y(self) -> int:
        return 0

    def screen(self):
        return QApplication.primaryScreen()

    def attach_bubble(self, bubble) -> None:
        self._bubble = bubble

    def sync_mirror(self) -> None:
        pass

    def is_mirrored(self) -> bool:
        return self._stage.mirrored


class PreviewStage(QWidget):
    """模拟桌面：壁纸 + 任务栏 + 宠物 + 真实气泡，可拖动宠物。"""

    positionChanged = Signal(int)   # 宠物水平位置 0-100（拖动时同步给滑杆）
    MARGIN = 12
    TASKBAR_H = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(580, 520)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("拖动宠物左右移动：跨过中线会自动镜像，气泡尾迹随之换边")

        self._dpr = max(1.0, QApplication.primaryScreen().devicePixelRatio())
        self._source = QPixmap(str(config.BASE_DIR / config.PET_IMAGE))
        if self._source.isNull():
            raise RuntimeError(f"无法加载宠物图片：{config.PET_IMAGE}")
        self._top_ratio, self._bottom_ratio = self._scan_content()

        self._values = dict(DEFAULTS)
        self._percent = 37.4
        self._low = False
        self._ratio = 0.42
        self._mirror = False
        self._phase = 0.0
        self._breath_phase = 0.0
        self._float_off = 0.0
        self._breath = 1.0
        self._dragging = False
        self._zoom = 1.0
        self._pet_pm: QPixmap | None = None
        self._pet_pm_key: tuple[int, int] | None = None
        self._bubble_pm: QPixmap | None = None
        self._bubble_key: tuple[bool, bool] | None = None

        self._pet_w, self._pet_h = self._size_for(float(self._values["PET_SCALE"]))
        self._stub = _PreviewPet(self)
        self._bubble = BubbleWindow(self._stub)   # 隐藏窗口，仅用于 render
        self._bubble.show_snapshot(self._snapshot())
        self._bubble.set_low(False)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._sync_timer()
        self.update()

    # ------------------------------ 资源 / 尺寸 ------------------------------

    def _scan_content(self) -> tuple[float, float]:
        """扫描 alpha，得到角色实际内容的上下边界（占图片高度的比例）。"""
        img = self._source.toImage().convertToFormat(QImage.Format_ARGB32)
        buf = bytes(img.constBits())
        stride = img.bytesPerLine()
        rows = [max(buf[y * stride + 3:(y + 1) * stride:4]) > 8
                for y in range(img.height())]
        top = next(i for i, v in enumerate(rows) if v)
        bottom = img.height() - 1 - next(i for i, v in enumerate(reversed(rows)) if v)
        return top / img.height(), (bottom + 1) / img.height()

    def _size_for(self, scale: float) -> tuple[int, int]:
        width = max(32, round(self._source.width() * float(scale)))
        height = max(32, round(self._source.height() * width / self._source.width()))
        return width, height

    def _margins(self) -> tuple[int, int]:
        """与 PetWindow._load_pixmap 相同的呼吸 / Q 弹留白。"""
        breath = float(self._values["BREATH_SCALE"])
        extra_x = self._pet_w * (breath / 2.0 + CLICK_SQUASH_W) / 2.0
        extra_y = self._pet_h * (breath / 2.0 + CLICK_SQUASH_H) / 2.0
        return int(math.ceil(extra_x)) + 3, int(math.ceil(extra_y)) + 3

    def pet_window_width(self) -> int:
        return self._pet_w + 2 * self._margins()[0]

    def pet_window_height(self) -> int:
        return self._pet_h + 2 * self._margins()[1]

    @property
    def mirrored(self) -> bool:
        return self._mirror

    # ------------------------------ 外部接口 ------------------------------

    def configure(self, values: dict[str, float], percent: float, low: bool) -> None:
        """更新全部可调数值并重排预览。"""
        self._values = dict(values)
        new_w, new_h = self._size_for(float(values["PET_SCALE"]))
        if (new_w, new_h) != (self._pet_w, self._pet_h):
            self._pet_w, self._pet_h = new_w, new_h
            self._stub._pet_w = new_w
            self._bubble.reload_layout()
            self._bubble_pm = None
            self._bubble_key = None
            self._pet_pm = None
        if abs(percent - self._percent) > 1e-9:
            self._percent = percent
            self._bubble.show_snapshot(self._snapshot())
            self._bubble_pm = None
            self._bubble_key = None
        if low != self._low:
            self._low = low
            self._bubble.set_low(low)
            self._bubble_pm = None
            self._bubble_key = None
        self._sync_timer()
        self.update()

    def set_position_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        changed = abs(ratio - self._ratio) > 1e-4
        self._ratio = ratio
        if changed:
            self.positionChanged.emit(round(ratio * 100))
        self.update()

    def info_text(self) -> str:
        """当前模拟的像素尺寸 / 偏移，写在舞台下方。"""
        layout = self._layout()
        mirror = "镜像" if layout["mirror"] else "常规"
        clamp = " · 已贴边" if layout["clamped"] else ""
        zoom = layout["zoom"]
        zoom_text = "" if zoom > 0.995 else f" ｜ 预览缩放 {zoom * 100:.0f}%"
        return (f"宠物 {self._pet_w}×{self._pet_h} px ｜ 窗口 {layout['pet_win_w']} px ｜ "
                f"气泡 {layout['win_w']}×{layout['win_h']} px ｜ "
                f"尾迹间距 {layout['tail_pad']} px ｜ 偏移 {layout['offset']:+d} px ｜ "
                f"{mirror}{clamp}{zoom_text}")

    # ------------------------------ 动画 ------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._timer.stop()   # 窗口不可见时不做动画，省 CPU
        super().hideEvent(event)

    def _sync_timer(self) -> None:
        amplitude = abs(float(self._values["FLOAT_AMPLITUDE_PX"]))
        breath = abs(float(self._values["BREATH_SCALE"]))
        if amplitude < 0.5 and breath < 1e-6:
            self._float_off = 0.0
            self._breath = 1.0
            self._timer.stop()
            return
        interval = max(16, round(1000 / max(5, int(self._values["ANIM_FPS"]))))
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        dt = self._timer.interval() / 1000.0
        period = max(0.5, float(self._values["FLOAT_PERIOD_SEC"]))
        self._phase = (self._phase + 2.0 * math.pi * dt / period) % (2.0 * math.pi)
        self._breath_phase = (self._breath_phase
                              + 2.0 * math.pi * dt / (period * BREATH_PERIOD_FACTOR)
                              ) % (2.0 * math.pi)
        self._float_off = round(-math.sin(self._phase)
                                * float(self._values["FLOAT_AMPLITUDE_PX"]))
        self._breath = 1.0 + math.sin(self._breath_phase) * (
            float(self._values["BREATH_SCALE"]) / 2.0)
        self.update()

    # ------------------------------ 模拟数据 ------------------------------

    def _snapshot(self):
        now = datetime.now(quota.CN_TZ)
        payload = {
            "viewer": {"auth_method": "sso"},
            "items": [{
                "product": "coding-plan",
                "edition": "personal",
                "subscribed": True,
                "periods": [
                    {"label": "session", "percent": self._percent,
                     "reset_at": (now + timedelta(hours=2)).isoformat()},
                    {"label": "weekly", "percent": max(0.0, self._percent * 0.4)},
                    {"label": "monthly", "percent": max(0.0, self._percent * 0.15)},
                ],
            }],
        }
        return quota.parse_arkcli_payload(payload, "coding-plan")

    # ------------------------------ 布局 ------------------------------

    def _desktop(self) -> QRectF:
        return QRectF(self.MARGIN, self.MARGIN,
                      self.width() - 2 * self.MARGIN,
                      self.height() - 2 * self.MARGIN)

    def _layout(self) -> dict:
        desktop = self._desktop()
        metrics = self._bubble.layout_metrics()

        # 内容放不下时整体等比缩小显示（数值仍是真实像素），保证不会被舞台裁掉
        need_w = max(self._pet_w, metrics["win_w"]) + 48
        need_h = (self._pet_h + max(0, metrics["win_h"] + metrics["tail_pad"])
                  + self.TASKBAR_H + 56)
        zoom = min(1.0, desktop.width() / max(1.0, need_w),
                   desktop.height() / max(1.0, need_h))
        self._zoom = max(0.35, zoom)
        view = QRectF(0, 0, desktop.width() / self._zoom,
                      desktop.height() / self._zoom)

        ground = view.bottom() - self.TASKBAR_H - 8
        # 左右留出半个宠物宽：0% / 100% 时宠物刚好完整贴在两侧
        half = self._pet_w / 2.0 + 6
        x0 = view.left() + half
        x1 = max(x0, view.right() - half)
        pet_cx = x0 + self._ratio * max(1.0, x1 - x0)
        pet_top = (ground - self._bottom_ratio * self._pet_h + self._float_off)
        content_top = pet_top + self._top_ratio * self._pet_h
        content_bottom = pet_top + self._bottom_ratio * self._pet_h
        pet_win_w = self.pet_window_width()

        self._mirror = pet_cx > (view.left() + view.width() / 2.0)
        flip_x = self._mirror != bool(config.BUBBLE_DEFAULT_FLIP_X)
        win_w, win_h = metrics["win_w"], metrics["win_h"]
        offset_ratio = (self._values["BUBBLE_MIRROR_OFFSET_X_RATIO"] if flip_x
                        else self._values["BUBBLE_OFFSET_X_RATIO"])
        offset = round(pet_win_w * float(offset_ratio))
        anchor = self._bubble.tail_anchor_x(flip_x)
        x = round(pet_cx - anchor + offset)
        left_limit, right_limit = view.left() + 4, view.right() - win_w - 4
        clamped = False
        if x < left_limit:
            x, clamped = round(left_limit), True
        elif x > right_limit:
            x, clamped = round(max(left_limit, right_limit)), True

        tail_pad = metrics["tail_pad"]
        above = content_top - tail_pad - win_h
        flip_down = above < view.top() + 4
        if not flip_down:
            y = above
        else:
            y = min(content_bottom + tail_pad,
                    view.bottom() - self.TASKBAR_H - win_h - 4)
            clamped = clamped or y < view.top() + 4
        return {
            "desktop": desktop, "view": view, "zoom": self._zoom,
            "ground": ground, "pet_cx": pet_cx,
            "pet_top": pet_top, "content_top": content_top,
            "content_bottom": content_bottom, "pet_win_w": pet_win_w,
            "win_w": win_w, "win_h": win_h, "tail_pad": tail_pad,
            "x": x, "y": y, "flip_x": flip_x, "flip_down": flip_down,
            "offset": offset, "anchor": anchor, "mirror": self._mirror,
            "clamped": clamped,
        }

    # ------------------------------ 绘制 ------------------------------

    def _pet_pixmap(self) -> QPixmap:
        key = (self._pet_w, self._pet_h)
        if self._pet_pm is None or self._pet_pm_key != key:
            pm = self._source.scaled(round(self._pet_w * self._dpr),
                                     round(self._pet_h * self._dpr),
                                     Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(self._dpr)
            self._pet_pm = pm
            self._pet_pm_key = key
        return self._pet_pm

    def _bubble_pixmap(self, flip_x: bool, flip_down: bool) -> QPixmap:
        key = (flip_x, flip_down)
        if self._bubble_pm is None or self._bubble_key != key:
            metrics = self._bubble.layout_metrics()
            width, height = metrics["win_w"], metrics["win_h"]
            pm = QPixmap(max(1, round(width * self._dpr)),
                         max(1, round(height * self._dpr)))
            pm.setDevicePixelRatio(self._dpr)
            pm.fill(Qt.transparent)
            self._bubble.set_preview_state(flip_x, flip_down)
            painter = QPainter(pm)
            self._bubble.render(painter, QPoint(0, 0))
            painter.end()
            self._bubble_pm = pm
            self._bubble_key = key
        return self._bubble_pm

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        layout = self._layout()
        desktop = layout["desktop"]
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)

        frame = QPainterPath()
        frame.addRoundedRect(desktop, 14.0, 14.0)
        p.save()
        p.setClipPath(frame)

        # 壁纸（真实坐标，不随预览缩放）
        wall = QLinearGradient(desktop.topLeft(), desktop.bottomRight())
        wall.setColorAt(0.0, QColor("#F4F7FF"))
        wall.setColorAt(0.55, QColor("#E8EFFF"))
        wall.setColorAt(1.0, QColor("#DAE5FB"))
        p.fillPath(frame, wall)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 96))
        p.drawEllipse(QPointF(desktop.left() + desktop.width() * 0.18,
                              desktop.top() + desktop.height() * 0.20), 130, 130)
        p.setBrush(QColor(77, 107, 254, 16))
        p.drawEllipse(QPointF(desktop.left() + desktop.width() * 0.84,
                              desktop.top() + desktop.height() * 0.12), 160, 160)

        # 场景：虚拟坐标 → 真实坐标（内容过大时整体缩小）
        p.save()
        p.translate(desktop.left(), desktop.top())
        p.scale(layout["zoom"], layout["zoom"])
        view = layout["view"]
        zoom = layout["zoom"]
        px = 1.0 / zoom  # 让 1px 线条 / 角标在缩放后保持真实尺寸

        # 任务栏
        taskbar = QRectF(view.left(), view.bottom() - self.TASKBAR_H,
                         view.width(), self.TASKBAR_H)
        p.fillRect(taskbar, QColor(255, 255, 255, 170))
        p.setPen(QPen(QColor("#D7DFF3"), px))
        p.drawLine(QPointF(taskbar.left(), taskbar.top()),
                   QPointF(taskbar.right(), taskbar.top()))
        p.setFont(_ui_font(min(22, max(8, round(11 * px)))))
        p.setPen(QColor("#9AA4C4"))
        p.drawText(taskbar.adjusted(12 * px, 0, -12 * px, 0),
                   Qt.AlignVCenter | Qt.AlignLeft, "任务栏")

        # 头顶参考线
        p.setPen(QPen(QColor(77, 107, 254, 90), px, Qt.DashLine))
        p.drawLine(QPointF(layout["pet_cx"], layout["content_top"]),
                   QPointF(layout["pet_cx"], layout["ground"]))

        # 影子 + 宠物
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(44, 58, 122, 26))
        p.drawEllipse(QPointF(layout["pet_cx"], layout["ground"] + 2),
                      self._pet_w * 0.32, max(4.0, self._pet_h * 0.022))
        p.save()
        p.translate(layout["pet_cx"], layout["content_bottom"])
        p.scale(self._breath * (-1.0 if layout["mirror"] else 1.0), self._breath)
        p.translate(-layout["pet_cx"], -layout["content_bottom"])
        p.drawPixmap(QPointF(layout["pet_cx"] - self._pet_w / 2.0,
                             layout["pet_top"]), self._pet_pixmap())
        p.restore()

        # 低额度 ! 角标（不随呼吸缩放）
        if self._low:
            p.setRenderHint(QPainter.Antialiasing, True)
            diameter = 22.0 * px
            x = layout["pet_cx"] + self._pet_w / 2.0 - diameter - 4 * px
            y = layout["content_top"] + 6 * px
            p.setBrush(QColor("#D0343F"))
            p.setPen(QPen(QColor("#FFFFFF"), 2.0 * px))
            p.drawEllipse(QRectF(x, y, diameter, diameter))
            p.setPen(QColor("#FFFFFF"))
            p.setFont(_ui_font(min(26, max(9, round(13 * px))), True))
            p.drawText(QRectF(x, y, diameter, diameter), Qt.AlignCenter, "!")

        # 气泡（真实 BubbleWindow 的绘制结果）
        p.drawPixmap(QPointF(layout["x"], layout["y"]),
                     self._bubble_pixmap(layout["flip_x"], layout["flip_down"]))
        p.restore()

        # 偏移标注用真实坐标画，避免缩放后字太小
        self._draw_offset_arrow(p, layout)

        p.restore()  # 取消圆角裁剪

        # 外框
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#DEE5F6"), 1.0))
        p.drawPath(frame)
        p.end()

    def _draw_offset_arrow(self, p: QPainter, layout: dict) -> None:
        """画出「尾迹中心 ↔ 宠物中心」的偏移量，数值和真实定位一致。"""
        zoom = layout["zoom"]
        origin = layout["desktop"].topLeft()
        pet_cx = origin.x() + layout["pet_cx"] * zoom
        tail_cx = origin.x() + (layout["x"] + layout["anchor"]) * zoom
        if abs(tail_cx - pet_cx) < 3:
            return
        top = origin.y() + layout["y"] * zoom
        bottom = origin.y() + (layout["y"] + layout["win_h"]) * zoom
        if top - 18 > origin.y() + 10:
            line_y = top - 14
        else:
            line_y = min(bottom + 14,
                         origin.y() + layout["desktop"].height() - self.TASKBAR_H * zoom - 6)

        pen = QPen(QColor(77, 107, 254, 150), 1.0, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(pet_cx, line_y), QPointF(tail_cx, line_y))
        for x in (pet_cx, tail_cx):
            p.drawLine(QPointF(x, line_y - 4), QPointF(x, line_y + 4))

        text = f"{layout['offset']:+d} px"
        p.setFont(_ui_font(10, True))
        metrics = p.fontMetrics()
        width = metrics.horizontalAdvance(text) + 12
        rect = QRectF((pet_cx + tail_cx) / 2.0 - width / 2.0, line_y - 19, width, 15)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 214))
        p.drawRoundedRect(rect, 7.0, 7.0)
        p.setPen(QColor("#4D6BFE"))
        p.drawText(rect, Qt.AlignCenter, text)

    # ------------------------------ 拖动 ------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self._move_to(event.position().x())
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self._dragging:
            self._move_to(event.position().x())
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            event.accept()

    def _move_to(self, x: float) -> None:
        desktop = self._desktop()
        zoom = max(0.35, self._zoom)
        view_width = desktop.width() / zoom
        half = self._pet_w / 2.0 + 6
        x0 = half
        x1 = max(x0, view_width - half)
        virtual_x = (float(x) - desktop.left()) / zoom
        ratio = (virtual_x - x0) / max(1.0, x1 - x0)
        ratio = max(0.0, min(1.0, ratio))
        if abs(ratio - self._ratio) < 1e-4:
            return
        self._ratio = ratio
        self.positionChanged.emit(round(ratio * 100))
        self.update()


# ============================ 配置对话框 ============================

class ConfigPreviewDialog(QDialog):
    """数值填写 + 模拟预览；可选连到正在运行的宠物做「应用到桌宠」。"""

    def __init__(self, pet=None, bubble=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConfigPreview")
        self.setWindowTitle(f"{config.APP_NAME} · 配置预览")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(1020, 720)
        self.resize(1180, 860)

        self._pet = pet
        self._bubble = bubble
        self._spins: dict[str, QDoubleSpinBox | QSpinBox] = {}
        self._loading = False

        self._build_ui()
        self._load_values({key: self._current(key) for key in DEFAULTS})
        self._refresh()
        self._center_on_screen(parent)

    def _center_on_screen(self, parent: QWidget | None) -> None:
        """放在宠物所在显示器的中间，并按屏幕大小收敛尺寸。"""
        screen = (parent.screen() if parent is not None else None) or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = min(self.width(), max(320, geo.width() - 40))
        height = min(self.height(), max(240, geo.height() - 40))
        self.resize(width, height)
        self.move(geo.left() + (geo.width() - width) // 2,
                  geo.top() + (geo.height() - height) // 2)

    # ------------------------------ 构建界面 ------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 14)
        root.setSpacing(9)

        title = QLabel("配置预览")
        title.setObjectName("title")
        subtitle = QLabel("左侧数值对应 config.py，右侧按真实像素模拟；改完可应用或写回文件。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addWidget(self._build_settings(), 0)
        body.addLayout(self._build_preview(), 1)
        root.addLayout(body, 1)
        root.addLayout(self._build_footer())

    def _build_settings(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(352)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("scrollBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 10, 4)
        layout.setSpacing(8)
        for group_title, fields in GROUPS:
            box = QGroupBox(group_title)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(14, 16, 14, 10)
            box_layout.setSpacing(8)
            for field in fields:
                box_layout.addWidget(self._field_row(field))
            layout.addWidget(box)
        layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _field_row(self, field: Field) -> QWidget:
        row = QWidget()
        column = QVBoxLayout(row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        line = QHBoxLayout()
        line.setSpacing(8)
        label = QLabel(field.label)
        label.setObjectName("fieldLabel")
        line.addWidget(label)
        line.addStretch(1)
        line.addWidget(self._make_spin(field))
        column.addLayout(line)

        if field.hint:
            hint = QLabel(field.hint)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            column.addWidget(hint)
        return row

    def _make_spin(self, field: Field) -> QDoubleSpinBox | QSpinBox:
        if field.is_int:
            spin: QDoubleSpinBox | QSpinBox = QSpinBox()
            spin.setRange(int(field.minimum), int(field.maximum))
            spin.setSingleStep(max(1, int(field.step)))
        else:
            spin = QDoubleSpinBox()
            spin.setRange(field.minimum, field.maximum)
            spin.setSingleStep(field.step)
            spin.setDecimals(field.decimals)
        spin.setAlignment(Qt.AlignRight)
        spin.setToolTip(f"{field.key}（config.py）")
        spin.valueChanged.connect(lambda *_: self._refresh())
        self._spins[field.key] = spin
        return spin

    def _build_preview(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(9)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("模拟额度"))
        self._percent = QDoubleSpinBox()
        self._percent.setRange(0.0, 100.0)
        self._percent.setDecimals(1)
        self._percent.setSingleStep(1.0)
        self._percent.setSuffix(" %")
        self._percent.setValue(37.4)
        self._percent.setFixedWidth(96)
        self._percent.setAlignment(Qt.AlignRight)
        self._percent.setToolTip("只影响预览显示，不写入 config.py")
        self._percent.valueChanged.connect(lambda *_: self._refresh())
        bar.addWidget(self._percent)
        bar.addSpacing(12)

        bar.addWidget(QLabel("水平位置"))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(0, 100)
        self._slider.setValue(42)
        self._slider.setFixedWidth(150)
        self._slider.setToolTip("模拟宠物在屏幕上的左右位置：右半边会触发镜像")
        self._slider.valueChanged.connect(self._on_slider)
        bar.addWidget(self._slider)
        self._pos_label = QLabel("42%")
        self._pos_label.setObjectName("hint")
        self._pos_label.setFixedWidth(38)
        bar.addWidget(self._pos_label)
        bar.addStretch(1)
        column.addLayout(bar)

        self._stage = PreviewStage(self)
        self._stage.positionChanged.connect(self._on_stage_drag)
        column.addWidget(self._stage, 1)

        self._info = QLabel("")
        self._info.setObjectName("info")
        self._info.setWordWrap(True)
        column.addWidget(self._info)

        tip = QLabel("拖动预览里的宠物：跨过中线自动镜像，气泡尾迹与偏移同步换边；"
                     "贴边时按真实屏幕的夹取逻辑处理。")
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        column.addWidget(tip)
        return column

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self._status = QLabel("")
        self._status.setObjectName("status")
        self._status.setWordWrap(False)
        row.addWidget(self._status, 1)

        restore = QPushButton("恢复默认")
        restore.clicked.connect(self._restore_defaults)
        row.addWidget(restore)

        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        row.addWidget(close)

        self._apply = QPushButton("应用到桌宠")
        self._apply.setEnabled(self._pet is not None)
        self._apply.setToolTip("只改当前运行的桌宠，不写文件" if self._pet is not None
                               else "当前没有运行中的桌宠（独立预览模式）")
        self._apply.clicked.connect(self._apply_runtime)
        row.addWidget(self._apply)

        save = QPushButton("保存到 config.py")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        return row

    # ------------------------------ 数值读写 ------------------------------

    @staticmethod
    def _current(key: str) -> float | int:
        return getattr(config, key, DEFAULTS[key])

    def _load_values(self, values: dict[str, float]) -> None:
        self._loading = True
        for key, spin in self._spins.items():
            if key in values:
                spin.setValue(values[key])
        self._loading = False

    def values(self) -> dict[str, float]:
        return {key: spin.value() for key, spin in self._spins.items()}

    def _low_now(self, values: dict[str, float]) -> bool:
        threshold = float(values["LOW_QUOTA_REMAINING_PERCENT"])
        remaining = 100.0 - float(self._percent.value())
        return threshold > 0 and remaining <= threshold + 1e-9

    def _refresh(self) -> None:
        if self._loading:
            return
        values = self.values()
        low = self._low_now(values)
        self._stage.configure(values, float(self._percent.value()), low)
        self._info.setText(self._stage.info_text())
        if self._dirty():
            self._set_status("有未保存的修改 · 可先「应用到桌宠」看真实效果，再保存", "#C8582F")
        else:
            self._set_status("当前数值与 config.py 一致", "#2E9E5B")

    def _dirty(self) -> bool:
        for key, value in self.values().items():
            if abs(float(value) - float(self._current(key))) > 1e-9:
                return True
        return False

    def _set_status(self, text: str, color: str = "#6B7699") -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().resizeEvent(event)
        if hasattr(self, "_info"):
            # 舞台尺寸变化会影响自动缩放比例，同步刷新尺寸标注
            self._refresh_info()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(event)
        QTimer.singleShot(0, self._refresh_info)

    def _refresh_info(self) -> None:
        self._info.setText(self._stage.info_text())

    # ------------------------------ 交互 ------------------------------

    def _on_slider(self, value: int) -> None:
        self._pos_label.setText(f"{value}%")
        self._stage.set_position_ratio(value / 100.0)
        self._info.setText(self._stage.info_text())

    def _on_stage_drag(self, value: int) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(value)
        self._slider.blockSignals(False)
        self._pos_label.setText(f"{value}%")
        self._info.setText(self._stage.info_text())

    def _restore_defaults(self) -> None:
        self._load_values(DEFAULTS)
        self._refresh()
        self._set_status("已恢复出厂默认值（还没写入文件）", "#C8582F")

    def _apply_runtime(self) -> None:
        values = self.values()
        start_changed = any(
            abs(float(values[key]) - float(self._current(key))) > 1e-9
            for key in ("START_DX", "START_DY")
        )
        for key, value in values.items():
            setattr(config, key, value)
        if self._pet is not None:
            self._pet.reload_appearance()
            if start_changed:
                # START_DX / START_DY 只在定位时读取；立即重排，避免必须重启才生效。
                self._pet.move_to_start()
        if self._bubble is not None:
            self._bubble.reload_layout()
        detail = "，启动位置已同步" if start_changed else ""
        self._set_status(f"已应用到当前桌宠（内存里生效{detail}，未写入 config.py）",
                         "#2E9E5B")

    def _save(self) -> None:
        path = Path(config.__file__).resolve()
        try:
            backup = save_config_values(path, self.values())
        except (OSError, ValueError) as exc:
            log.warning("写入 config.py 失败：%s", exc)
            self._set_status(f"写入失败：{exc}", "#B23A48")
            return
        self._apply_runtime()
        self._set_status(f"已写入 {path.name}（备份 {backup.name}）并应用到桌宠，重启后依然生效",
                         "#2E9E5B")
        log.info("配置已保存：%s（备份 %s）", path, backup)


def run() -> int:
    """单独运行本文件时的预览入口（不依赖桌宠主程序）。"""
    app = QApplication(sys.argv)
    dialog = ConfigPreviewDialog()
    dialog.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
