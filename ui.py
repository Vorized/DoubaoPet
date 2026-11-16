# -*- coding: utf-8 -*-
"""透明置顶豆包桌宠窗口：漂浮 + 呼吸动画、拖拽移动、右键菜单、额度气泡。

状态模式：
- 悬浮（默认）：呼吸 + 漂浮；重力模式：完全静态、以任务栏为地面、拖到高处松手自动下落。

性能设计（低占用关键）：
- 漂浮动画：直接移动窗口位置（由 DWM 合成，开销极低），不触发任何重绘。
- 呼吸动画：低频重绘（默认 10fps），且只在需要时才刷新。
- 点击反馈：点击后约 0.45 秒内恢复全帧率重绘，随后自动降频。
- 镜像翻转：约 0.22 秒过渡（仅翻转期间临时提高帧率），结束后立即回到常规帧率。
- 静态场景（重力静止 / 浮动幅度与呼吸均为 0 时）零重绘、零窗口移动，CPU 占用接近 0。
"""
from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timedelta

from PySide6.QtCore import QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QIcon, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF, QTransform)
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

import config
import quota

log = logging.getLogger("pet.ui")

# ---------- 性能参数（开发调优用；一般不需要修改） ----------
BREATH_FPS = 10            # 呼吸重绘频率上限（帧/秒）
CLICK_PULSE_SEC = 0.45     # 点击 Q 弹持续时间（秒）
CLICK_SQUASH_W = 0.12      # 点击瞬间宽度扩张比例（向两侧撑开）
CLICK_SQUASH_H = 0.15      # 点击瞬间高度压缩比例（向下压扁，脚底不动）
CLICK_WOBBLE_CYCLES = 1.6  # 回弹摆动周期数（越大越弹）
CLICK_WOBBLE_DECAY = 3.2   # 回弹衰减速度（越大越快停）
BREATH_PERIOD_FACTOR = 1.5  # 呼吸周期 = 浮动周期 × 1.5
SMOOTH_SCALE = False       # 呼吸缩放用平滑插值（True 画质更好；False 更省 CPU，常规画质几乎无差）
QUANTIZE_BREATH = True     # 仅在物理像素尺寸变化时才重绘（省 CPU 的关键开关）

# ---------- 镜像过渡（追加功能） ----------
MIRROR_FLIP_SEC = 0.22     # 镜像翻转过渡时长（秒；60fps 下约 13 帧）
MIRROR_FLIP_STRETCH = 0.05  # 翻转中点的竖直拉伸（5% = 更像“转身”）

# ---------- 气泡参数 ----------
BUBBLE_WIDTH_RATIO = 1.0  # 气泡主体宽度 ÷ 宠物宽度
BUBBLE_UI_REF_WIDTH = 320  # 气泡排版的设计基准宽度；文字 / 进度条 / 行高按实际宽度等比缩放
BUBBLE_BODY_ASPECT = 0.64  # 卡片高 ÷ 宽（演示版比例，按 5 行内容略微加高）
BUBBLE_RADIUS = 20         # 卡片圆角（设计稿像素）
BUBBLE_PAD_X = 20          # 卡片左右内边距（设计稿像素）
BUBBLE_BORDER_W = 1.0      # 卡片描边
BUBBLE_TAIL_W = 24         # 尾迹底边宽（设计稿像素）
BUBBLE_TAIL_H = 13         # 尾迹高（设计稿像素）
BUBBLE_TAIL_X_RATIO = 0.42  # 尾迹中心在卡片宽度上的位置
BUBBLE_TAIL_PAD_PX = -32     # 尾迹尖端与角色画面内容（头顶 / 脚底）的间距

# 卡片配色（与演示动画一致）
BUBBLE_CARD_COLOR = "#FFFFFF"
BUBBLE_BORDER_COLOR = "#DDE3F5"
BUBBLE_TITLE_COLOR = "#6B7699"
BUBBLE_SUB_COLOR = "#6B7699"
BUBBLE_HINT_COLOR = "#98A0BD"
BUBBLE_INK = "#232A4D"
BUBBLE_TRACK_COLOR = "#E9EDF9"
BUBBLE_SHADOW = (44, 58, 122)
BUBBLE_FADE_MS = 150       # 淡入淡出时长（毫秒）
CLICK_EGG_FONT_PX = 40      # 彩蛋字号（比百分比的 34 明显更大）
KEYCAST_BUBBLE_SCALE = 0.80  # 按键显示气泡相对普通气泡的缩放（仅键帽，给键帽留出足够空间）


def _egg_replies() -> list[str]:
    """读取 config.CLICK_EGG_REPLIES，容忍字符串 / 空值并保证至少一条。"""
    raw = getattr(config, "CLICK_EGG_REPLIES", [])
    if isinstance(raw, str):
        raw = [raw]
    try:
        values = [str(item).strip() for item in raw]
    except TypeError:
        values = []
    return [item for item in values if item] or ["豆包豆包"]

# ---------- 额度进度条 ----------
QUOTA_TITLE_REMAINING = "豆包 · 本月剩余额度"   # 没有套餐名 / 窗口信息时的兜底
QUOTA_TITLE_USED = "豆包 · 本月已用额度"
QUOTA_FILL_COLOR = "#4D6BFE"       # 进度条正常色（左）
QUOTA_FILL_COLOR_2 = "#8B5CF6"     # 进度条正常色（右，渐变）
QUOTA_TRACK_COLOR = BUBBLE_TRACK_COLOR  # 进度条底槽
QUOTA_WARN_COLOR = "#E8A23D"       # 剩余额度偏低（橙色）
QUOTA_BAR_H = 9                    # 进度条高度（逻辑像素）
QUOTA_BAR_RATIO = 0.88             # 进度条宽度 ÷ 气泡主体宽度


def _quota_title(product_name: str, window_label: str, show_remaining: bool,
                 demo: bool) -> str:
    """气泡标题：套餐名 · 主窗口（最紧窗口）剩余 / 已用额度。

    主数字取最紧窗口（已用百分比最高），所以标题必须标出窗口，否则用户
    会把"5 小时窗口剩余 8%"误读成"本月还剩 92%"。
    """
    plan = str(product_name or "").split(" · ")[0].strip()
    suffix = "剩余额度" if show_remaining else "已用额度"
    if plan and window_label:
        middle = "本月" if window_label == "月" else f"{window_label}窗口"
        title = f"{plan} · {middle}{suffix}"
    elif plan:
        title = f"{plan} · {suffix}"
    else:
        title = QUOTA_TITLE_REMAINING if show_remaining else QUOTA_TITLE_USED
    return title + ("（演示）" if demo else "")

# ---------- 提醒视觉 ----------
DELTA_UP_COLOR = "#2E9E5B"      # 额度消耗提示（绿：正常消耗）
DELTA_RESET_COLOR = "#4D6BFE"   # 额度重置提示（蓝）
DELTA_ADJUST_COLOR = "#C8582F"  # 数据修正提示（橙）
LOW_BALANCE_COLOR = "#D0343F"   # 低额度警示（红）
LOW_BADGE_PX = 26               # 宠物低额度角标直径（逻辑像素）

# ---------- 状态模式 / 重力（追加功能） ----------
MODE_FLOAT = "float"          # 悬浮：呼吸 + 漂浮动画（原有行为）
MODE_GRAVITY = "gravity"      # 重力：静态画面、以任务栏为地面、拖高松手自动下落
GRAVITY_ACCEL_PX_S2 = 3000    # 下落加速度（逻辑像素 / 秒²）
GRAVITY_MAX_V_PX_S = 3200     # 下落速度上限（逻辑像素 / 秒）
GRAVITY_FPS = 60              # 下落期间的刷新帧率（60 = 最顺滑；调低更省 CPU）
GRAVITY_BOUNCE_RESTITUTION = 0.4    # 落地反弹：反弹速度 = 落地速度 × 该比例
GRAVITY_BOUNCE_MIN_V_PX_S = 300     # 落地速度低于此值不再反弹（自然停住）
GRAVITY_BOUNCE_SQUASH_FULL_V = 1200  # 达到最大触地形变所需的落地速度


class PetWindow(QWidget):
    """桌宠主窗口。"""

    clicked = Signal()    # 左键点击（非拖拽）时发出；M2 起用于“点击立即刷新额度”
    hovered = Signal()    # 鼠标进入宠物（触发悬停刷新 + 弹气泡）
    unhovered = Signal()  # 鼠标离开宠物
    dragged = Signal()    # 开始拖拽（第一次超过阈值）
    moved = Signal(int, int)  # 窗口位置变化 (dx, dy)：供气泡跟随漂浮

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(config.APP_NAME)

        flags = Qt.FramelessWindowHint | Qt.Tool  # Tool：不进入任务栏 / Alt+Tab
        if config.ALWAYS_ON_TOP:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # 启动时不抢焦点

        self._load_pixmap()

        # 动画状态
        self._fps = max(1, min(60, int(config.ANIM_FPS)))
        self._breath_every = max(1, round(self._fps / max(1, int(BREATH_FPS))))
        self._period = max(0.5, float(config.FLOAT_PERIOD_SEC))
        self._float_phase = 0.0    # 漂浮相位
        self._breath_phase = 0.0   # 呼吸相位
        self._breath_tick = 0      # 呼吸降频计数
        self._last_q_size = -1     # 量化重绘：上次物理像素尺寸
        self._click_pulse = 0.0    # 点击 / 触地反馈强度：1 → 0
        self._impact_strength = 1.0  # 形变幅度系数（重力触地时按落地速度衰减）
        self._base_interval = max(1, round(1000 / self._fps))  # 常规帧间隔（重力下落时临时提高帧率）
        self._timer = QTimer(self)
        self._timer.setInterval(self._base_interval)
        self._timer.timeout.connect(self._tick)

        # 状态模式（启动值由 config.PET_MODE 决定；运行中可右键菜单切换）
        self._mode = (MODE_GRAVITY if str(config.PET_MODE).strip().lower() == MODE_GRAVITY
                      else MODE_FLOAT)
        self._falling = False   # 是否正在下落
        self._fall_v = 0.0      # 下落速度（逻辑像素/秒）
        self._fall_y = 0.0      # 下落中的浮点 y（避免整数量化误差累积）

        # 位置：anchor 为“漂浮偏移为 0 时”的窗口位置
        self._anchor = QPoint()
        self._float_off = 0

        # 拖拽状态
        self._pressed = False
        self._press_global: QPoint | None = None
        self._drag_origin: QPoint | None = None
        self._moved = False

        # 水平镜像（宠物贴图 + 气泡尾迹共用；贴屏幕左缘时自动翻转）
        self._mirror = False
        self._mirror_scale = 1.0    # 当前贴图水平缩放：+1 常态 → -1 镜像（过渡中经过 0）
        self._mirror_from = 1.0
        self._mirror_to = 1.0
        self._mirror_t = 1.0        # 过渡进度 0 → 1
        self._mirror_anim = False   # 是否正在播放翻转过渡
        self._mirror_ready = False  # 首次确定镜像方向时就位（不播过渡）
        self._bubble = None

        # M4：低额度角标
        self._low_quota = False
        self._badge_font = QFont()
        self._badge_font.setPixelSize(15)
        self._badge_font.setBold(True)

        self._menu = self._build_menu()

        log.info("宠物状态：%s", "重力（静态 / 任务栏为地面）" if self._mode == MODE_GRAVITY
                 else "悬浮（呼吸 + 漂浮）")

    # ------------------------------ 外观加载 ------------------------------

    def _load_pixmap(self) -> None:
        pm = QPixmap(str(config.BASE_DIR / config.PET_IMAGE))
        if pm.isNull():
            raise RuntimeError(f"无法加载宠物图片：{config.PET_IMAGE}")

        # 非正方形贴图按宽度缩放，窗口也按实际宽高留白（豆包贴图是竖版）
        self._pet_w = max(32, round(pm.width() * float(config.PET_SCALE)))
        self._pet_h = max(32, round(pm.height() * self._pet_w / pm.width()))

        # 按屏幕像素密度预缩放，保证高分屏也清晰
        self._dpr = QApplication.primaryScreen().devicePixelRatio()
        scaled = pm.scaled(round(self._pet_w * self._dpr), round(self._pet_h * self._dpr),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(self._dpr)
        self._base_pixmap = scaled

        # 窗口内留白：只服务呼吸、点击 Q 弹与镜像翻转的缩放
        extra_x = self._pet_w * (float(config.BREATH_SCALE) / 2.0 + CLICK_SQUASH_W) / 2.0
        extra_y = self._pet_h * (float(config.BREATH_SCALE) / 2.0 + CLICK_SQUASH_H
                                 + MIRROR_FLIP_STRETCH) / 2.0
        self._margin_x = int(math.ceil(extra_x)) + 3
        self._margin_y = int(math.ceil(extra_y)) + 3
        self.resize(self._pet_w + 2 * self._margin_x,
                    self._pet_h + 2 * self._margin_y)
        self._pet_x = (self.width() - self._pet_w) / 2.0
        self._pet_y = (self.height() - self._pet_h) / 2.0

        # 扫描图片 alpha，找到角色的实际画面内容上下边界（气泡尾迹贴头定位用）
        img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
        iw, ih = img.width(), img.height()
        buf = bytes(img.constBits())
        stride = img.bytesPerLine()
        rows = [max(buf[y * stride + 3:(y + 1) * stride:4]) > 8 for y in range(ih)]
        top_px = next(i for i, v in enumerate(rows) if v)
        bottom_px = ih - 1 - next(i for i, v in enumerate(reversed(rows)) if v)
        ratio = self._pet_h / ih
        self._content_top = round(self._pet_y + top_px * ratio)
        self._content_bottom = round(self._pet_y + (bottom_px + 1) * ratio)

        log.info("宠物已加载：%s → %dx%dpx（屏幕缩放 %.0f%%；内容上边界 %d / 下边界 %d）",
                 config.PET_IMAGE, self._pet_w, self._pet_h, self._dpr * 100,
                 self._content_top, self._content_bottom)

    def reload_appearance(self) -> None:
        """按当前 config 重新加载贴图 / 尺寸 / 动画参数（配置预览「应用到桌宠」用）。

        以窗口中心为锚点重排，避免缩放后宠物跳出屏幕；重力模式下重新落回地面。
        """
        old_center = self.geometry().center()
        self._load_pixmap()
        self._fps = max(1, min(60, int(config.ANIM_FPS)))
        self._breath_every = max(1, round(self._fps / max(1, int(BREATH_FPS))))
        self._period = max(0.5, float(config.FLOAT_PERIOD_SEC))
        self._base_interval = max(1, round(1000 / self._fps))
        self._click_pulse = 0.0
        self._last_q_size = -1
        self._float_off = 0
        self.move(old_center.x() - self.width() // 2, old_center.y() - self.height() // 2)
        if self._mode == MODE_GRAVITY:
            self.move(self.x(), self._ground_y())
        self._anchor = self.pos()
        self._sync_interval()
        self.sync_mirror(animate=False)
        self.update()
        self._update_anim_timer()
        log.info("已按新配置重载外观：%dx%dpx", self._pet_w, self._pet_h)

    # ------------------------------ 右键菜单 ------------------------------

    def _build_menu(self) -> QMenu:
        menu = QMenu(self)
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(QApplication.instance().quit)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._menu.exec(event.globalPos())

    def pulse(self) -> None:
        """触发一次点击弹跳反馈（也被“二次启动唤起”场景用来打招呼）。"""
        self._click_pulse = 1.0
        self._impact_strength = 1.0
        self.update()
        self._update_anim_timer()  # 重力状态下临时启动重绘

    def set_context_menu(self, menu: QMenu) -> None:
        """替换右键菜单（M4 起与托盘共用同一份菜单）。"""
        self._menu = menu

    def set_low_quota(self, low: bool) -> None:
        """低额度角标开关（M4）；状态变化时立即重绘一次。"""
        if low != self._low_quota:
            self._low_quota = low
            self.update()

    # ------------------------------ 水平镜像 ------------------------------

    def attach_bubble(self, bubble) -> None:
        """气泡构造完成后接入：宠物贴图与气泡尾迹共用同一镜像状态。"""
        self._bubble = bubble
        self.sync_mirror(animate=False)  # 启动位置直接就位，不播翻转过渡
        self._mirror_ready = True

    def is_mirrored(self) -> bool:
        """当前宠物贴图是否水平镜像（气泡尾迹同步翻转）。"""
        return self._mirror

    def sync_mirror(self, animate: bool = True) -> None:
        """按当前 x 位置更新水平镜像。

        宠物中心位于当前显示器的右半边时，把贴图与尾迹一起镜像；
        这样角色在屏幕右侧时会朝向屏幕内侧，气泡尾迹也同步换边。

        角色贴图用一次约 MIRROR_FLIP_SEC 的“卡片翻转”过渡切换到新方向；
        首次就位（启动 / 接入气泡）直接给最终状态，不播过渡。
        """
        mirror = bool(self._bubble is not None and self._bubble.wants_mirror(self))
        if mirror == self._mirror:
            return
        self._mirror = mirror
        log.info("宠物水平镜像：%s", "开（显示器右侧，尾迹朝右）" if mirror else "关")

        target = -1.0 if mirror else 1.0
        if animate and self._mirror_ready and self.isVisible():
            # 从当前角度出发（连续反向时不会跳帧），平滑翻到新方向
            self._mirror_from = self._mirror_scale
            self._mirror_to = target
            self._mirror_t = 0.0
            self._mirror_anim = True
            self.update()
            self._sync_interval()      # 翻转期间临时 60fps
            self._update_anim_timer()
        else:
            self._mirror_scale = target
            self._mirror_anim = False
            self.update()

    # ------------------------------ 状态模式 / 重力 ------------------------------

    def is_gravity(self) -> bool:
        """当前是否为重力状态。"""
        return self._mode == MODE_GRAVITY

    def set_mode(self, mode: str) -> None:
        """切换悬浮 / 重力状态（右键菜单与托盘的共同入口）。"""
        mode = MODE_GRAVITY if str(mode).strip().lower() == MODE_GRAVITY else MODE_FLOAT
        if mode == self._mode:
            return
        self._mode = mode
        self._click_pulse = 0.0
        self._breath_phase = 0.0
        self._last_q_size = -1
        if mode == MODE_GRAVITY:
            log.info("宠物状态切换：重力（静态画面，松手后自动落回任务栏）")
            self._float_off = 0
            self.update()      # 立即按 1.0 比例重画一次，消除残留的呼吸缩放
            self.start_fall()  # 从当前位置落向地面
        else:
            log.info("宠物状态切换：悬浮（呼吸 + 漂浮）")
            self._falling = False
            self._fall_v = 0.0
            self._sync_interval()
            self._anchor = self.pos()
            self._float_off = 0
            self._float_phase = 0.0
            self.update()
            self._update_anim_timer()

    def start_fall(self) -> None:
        """开始（或立即完成）一次落向地面的动作。"""
        ground = self._ground_y()
        if self.y() >= ground:
            self._falling = False
            self._fall_v = 0.0
            if self.y() != ground:
                self.move(self.x(), ground)
                self._anchor = self.pos()
            self._update_anim_timer()
            return
        self._falling = True
        self._fall_v = 0.0
        self._fall_y = float(self.y())
        self._sync_interval()  # 下落期间提高帧率
        log.info("宠物开始下落：y=%d → 地面 y=%d", self.y(), ground)
        self._update_anim_timer()

    def _ground_y(self) -> int:
        """地面：任务栏顶部（可用区域底边）对齐角色画面内容底边。"""
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        return max(geo.top(), geo.bottom() + 1 - self._content_bottom)

    def _update_anim_timer(self, visible: bool | None = None) -> None:
        """按当前状态启停动画定时器（重力静止时完全停表，CPU 接近零）。"""
        if visible is None:
            visible = self.isVisible()
        if not visible:
            return
        self._sync_interval()
        need = (self._mode == MODE_FLOAT or self._falling
                or self._click_pulse > 0.0 or self._mirror_anim)
        if need and not self._timer.isActive():
            self._timer.start()
        elif not need and self._timer.isActive():
            self._timer.stop()

    def _sync_interval(self) -> None:
        """按当前动作选择帧率：下落 / 点击 Q 弹 / 镜像翻转期间 60fps，其余用常规帧率。"""
        fast = self._falling or self._click_pulse > 0.0 or self._mirror_anim
        ms = max(1, round(1000 / GRAVITY_FPS)) if fast else self._base_interval
        if self._timer.interval() != ms:
            self._timer.setInterval(ms)

    # ------------------------------ 动画 ------------------------------

    def _breath_scale(self) -> float:
        return 1.0 + math.sin(self._breath_phase) * (config.BREATH_SCALE / 2.0)

    def _tick(self) -> None:
        self._sync_interval()  # 点击 Q 弹结束后自动降回常规帧率
        dt = self._timer.interval() / 1000.0
        mirror_repaint = self._tick_mirror(dt)

        if self._mode == MODE_GRAVITY:  # 重力状态：只有下落、点击反馈与镜像翻转
            self._tick_gravity(dt)
            if mirror_repaint:
                self.update()
            return

        # 1) 漂浮：移动窗口（不重绘，开销极低）
        self._float_phase = (self._float_phase + 2.0 * math.pi * dt / self._period) % (2.0 * math.pi)
        if not self._pressed and config.FLOAT_AMPLITUDE_PX != 0:
            off = round(-math.sin(self._float_phase) * config.FLOAT_AMPLITUDE_PX)
            if off != self._float_off:
                self._float_off = off
                self.move(self._anchor + QPoint(0, off))

        # 2) 呼吸 / 点击反馈：低频重绘
        self._breath_phase = (self._breath_phase +
                              2.0 * math.pi * dt / (self._period * BREATH_PERIOD_FACTOR)) % (2.0 * math.pi)
        self._breath_tick += 1
        need_repaint = False
        if self._click_pulse > 0.0:
            self._click_pulse = max(0.0, self._click_pulse - dt / CLICK_PULSE_SEC)
            need_repaint = True  # 点击弹跳期间全帧率刷新
        elif config.BREATH_SCALE > 0 and self._breath_tick % self._breath_every == 0:
            if QUANTIZE_BREATH:
                q = round(self._pet_w * self._breath_scale() * self._dpr)
                if q != self._last_q_size:
                    self._last_q_size = q
                    need_repaint = True
            else:
                need_repaint = True
        if need_repaint or mirror_repaint:
            self.update()

    def _tick_mirror(self, dt: float) -> bool:
        """推进镜像翻转过渡；返回本帧是否需要重绘。

        余弦缓动：起步和收尾都慢，中点正好是“侧对屏幕”的 0°，看起来像转身。
        """
        if not self._mirror_anim:
            return False
        self._mirror_t = min(1.0, self._mirror_t + dt / max(0.01, MIRROR_FLIP_SEC))
        ease = 0.5 - 0.5 * math.cos(math.pi * self._mirror_t)
        self._mirror_scale = self._mirror_from + (self._mirror_to - self._mirror_from) * ease
        if self._mirror_t >= 1.0:
            self._mirror_scale = self._mirror_to
            self._mirror_anim = False
            self._sync_interval()  # 翻转结束立即降回常规帧率
        return True

    def _mirror_visual(self) -> tuple[float, float]:
        """当前镜像过渡的（水平缩放, 竖直拉伸）；静止时为（±1, 1）。"""
        if not self._mirror_anim:
            return self._mirror_scale, 1.0
        stretch = 1.0 + MIRROR_FLIP_STRETCH * (1.0 - abs(self._mirror_scale))
        return self._mirror_scale, stretch

    def _tick_gravity(self, dt: float) -> None:
        # 1) 下落 / 反弹：只移动窗口，不重绘
        if self._falling:
            self._fall_v = min(GRAVITY_MAX_V_PX_S, self._fall_v + GRAVITY_ACCEL_PX_S2 * dt)
            self._fall_y += self._fall_v * dt
            ground = self._ground_y()
            if self._fall_y >= ground:
                impact_v = abs(self._fall_v)
                self._fall_y = float(ground)
                # 触地 Q 弹压扁：幅度随落地速度缩放
                self._click_pulse = 1.0
                self._impact_strength = min(1.0, impact_v / GRAVITY_BOUNCE_SQUASH_FULL_V)
                if impact_v >= GRAVITY_BOUNCE_MIN_V_PX_S:  # 还有速度 → 反弹
                    self._fall_v = -impact_v * GRAVITY_BOUNCE_RESTITUTION
                    log.info("宠物触地反弹：落地速度 %.0f → 反弹速度 %.0f",
                             impact_v, -self._fall_v)
                else:  # 速度太小 → 自然停住
                    self._falling = False
                    self._fall_v = 0.0
                    self._sync_interval()  # 弹跳结束恢复常规帧率（触地形变仍在进行则保持 60fps）
                    log.info("宠物落地：y=%d", ground)
            self.move(self.x(), round(self._fall_y))
            self._anchor = self.pos()

        # 2) 点击弹跳（重力状态下唯一会重绘的瞬间）
        if self._click_pulse > 0.0:
            self._click_pulse = max(0.0, self._click_pulse - dt / CLICK_PULSE_SEC)
            self.update()

        # 3) 完全静止后停表（不再有任何定时开销）
        if not self._falling and self._click_pulse <= 0.0 and not self._mirror_anim:
            self._timer.stop()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        breath = 1.0 if self._mode == MODE_GRAVITY else self._breath_scale()
        sx, sy = self._click_squash()
        mirror, stretch = self._mirror_visual()

        painter = QPainter(self)
        # 点击 Q 弹 / 镜像翻转期间用平滑插值保证画质；常规呼吸用快速缩放省 CPU
        painter.setRenderHint(QPainter.SmoothPixmapTransform,
                              SMOOTH_SCALE or self._click_pulse > 0.0 or self._mirror_anim)
        # 以脚底为锚点缩放：压扁时脚不动、身体向下压，宽度向两侧撑开（回弹时轻微拉伸）
        anchor_y = float(self._content_bottom)
        painter.translate(self.width() / 2.0, anchor_y)
        painter.scale(breath * sx * mirror, breath * sy * stretch)
        painter.translate(-self.width() / 2.0, -anchor_y)

        painter.drawPixmap(QPointF(self._pet_x, self._pet_y), self._base_pixmap)

        if self._low_quota:  # 低额度角标（不随呼吸缩放）
            painter.resetTransform()
            d = LOW_BADGE_PX
            x = self.width() - self._margin_x - d - 2.0
            y = float(self._content_top) + 4.0
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(QColor(LOW_BALANCE_COLOR))
            painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
            painter.drawEllipse(QRectF(x, y, d, d))
            painter.setPen(QColor("#FFFFFF"))
            painter.setFont(self._badge_font)
            painter.drawText(QRectF(x, y, d, d), Qt.AlignCenter, "!")

    def _click_squash(self) -> tuple[float, float]:
        """点击 / 触地 Q 弹形变（非线性）：阻尼余弦摆动，宽向两侧扩张、高向下压缩。"""
        if self._click_pulse <= 0.0:
            return 1.0, 1.0
        u = 1.0 - self._click_pulse  # 0（触发瞬间）→ 1（动画结束）
        f = math.cos(2.0 * math.pi * CLICK_WOBBLE_CYCLES * u) * math.exp(-CLICK_WOBBLE_DECAY * u)
        w = CLICK_SQUASH_W * self._impact_strength
        h = CLICK_SQUASH_H * self._impact_strength
        return 1.0 + w * f, 1.0 - h * f

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._update_anim_timer(visible=True)  # showEvent 里 isVisible 时序不保证，显式传 True
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._timer.stop()  # 隐藏时停掉动画，不占 CPU
        super().hideEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        old, new = event.oldPos(), event.pos()
        dx, dy = new.x() - old.x(), new.y() - old.y()
        if dx or dy:
            self.moved.emit(dx, dy)  # 气泡跟随漂浮（只移动气泡窗口，不触发重绘）
        super().moveEvent(event)

    # ------------------------------ 鼠标交互 ------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton:
            if self._falling:  # 下落中按住可取消，直接接管为拖拽
                self._falling = False
                self._fall_v = 0.0
                self._sync_interval()
            self._pressed = True
            self._press_global = event.globalPosition().toPoint()
            self._drag_origin = self.pos()
            self._moved = False
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if not self._pressed or self._press_global is None or self._drag_origin is None:
            return
        delta = event.globalPosition().toPoint() - self._press_global
        if not self._moved and delta.manhattanLength() > 6:
            self._moved = True  # 超过阈值才算拖拽，否则视为点击
            self.dragged.emit()
        if self._moved:
            self.move(self._drag_origin + delta)
            self._anchor = self.pos() - QPoint(0, self._float_off)
            self.sync_mirror()  # 拖动经过左缘阈值时触发翻转过渡
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self._pressed:
            moved = self._moved
            self._pressed = False
            self._press_global = None
            self._drag_origin = None
            self._moved = False
            if moved:
                if self._mode == MODE_GRAVITY:
                    self.start_fall()  # 拖到高处松手 → 自动落回地面
                else:
                    self._anchor = self.pos() - QPoint(0, self._float_off)
            else:
                self._click_pulse = 1.0  # 点击 Q 弹反馈
                self._impact_strength = 1.0
                self._update_anim_timer()  # 重力状态下临时启动重绘
                self.clicked.emit()      # 通知外部立即刷新额度（在途请求自动防抖）
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self.hovered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self.unhovered.emit()
        super().leaveEvent(event)

    # ------------------------------ 位置 ------------------------------

    def move_to_start(self) -> None:
        """按配置的锚点移动到启动位置。"""
        geo = QApplication.primaryScreen().availableGeometry()
        anchor = str(config.START_ANCHOR).lower()
        dx, dy = int(config.START_DX), int(config.START_DY)
        if anchor == "bottom-left":
            x, y = geo.left() + dx, geo.bottom() - self.height() - dy
        elif anchor == "top-right":
            x, y = geo.right() - self.width() - dx, geo.top() + dy
        elif anchor == "top-left":
            x, y = geo.left() + dx, geo.top() + dy
        elif anchor == "center":
            x = geo.left() + (geo.width() - self.width()) // 2 + dx
            y = geo.top() + (geo.height() - self.height()) // 2 + dy
        else:  # bottom-right 及未知值
            if anchor != "bottom-right":
                log.warning("未知的 START_ANCHOR=%r，回退到右下角", anchor)
            x, y = geo.right() - self.width() - dx, geo.bottom() - self.height() - dy
        if self._mode == MODE_GRAVITY:
            y = self._ground_y()  # 重力状态：启动即站在任务栏（地面）上
        self.move(x, y)
        self._anchor = QPoint(x, y)
        self._float_off = 0
        self.sync_mirror()


class BubbleWindow(QWidget):
    """额度气泡：显示在宠物头顶，顶部空间不足时自动翻到下方；自动收起。

    - 内容用 show_snapshot / show_error / show_loading 更新；
    - 主数字显示“剩余”或“已用”百分比，下方为圆角进度条；
    - 点击气泡发出 clicked 信号（用于手动刷新）；
    - 图像素材 assets/bubble.png 在运行时自动切分为「主体 + 尾迹」两部分，
      保持原始手绘比例；镜像时主体与尾迹作为整体一起翻转（文字与进度条不镜像，
      最后叠加），竖直翻转时尾迹朝上、主体缺口也朝上。
    """

    clicked = Signal()

    def __init__(self, pet: PetWindow) -> None:
        super().__init__(None)
        self._pet = pet
        self.setWindowTitle(f"{config.APP_NAME} · 套餐额度")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)  # 弹出时不抢焦点

        self._load_pixmaps()
        self._load_fonts()

        # 内容状态（默认“查询中”）
        self._percent = "--"
        self._percent_value: float | None = None
        self._used_percent = 0.0
        self._remaining_percent = 100.0
        self._used: float | None = None
        self._total: float | None = None
        self._unit = ""
        self._sub = "首次查询中…"
        self._hint = ""
        self._title_override = ""   # 查询中 / 出错时的中性标题（正常态为空）
        self._product_name = ""
        self._source_label = ""
        self._window_label = ""   # 主窗口（最紧窗口）名：5 小时 / 会话 / 周 / 月
        self._reset_at = None
        self._demo = False
        self._bar_visible = False
        self._bar_value = 0.0
        self._amount_small = True
        self._amount_error = False
        self._flip = False  # True = 显示在宠物下方（尾迹朝上）
        # True = 水平镜像（尾迹朝右）；默认值由 config.BUBBLE_DEFAULT_FLIP_X 决定
        self._flip_x = bool(config.BUBBLE_DEFAULT_FLIP_X)
        self._delta_text = ""  # 相对上次刷新的变化（空串 = 不显示）
        self._delta_color = DELTA_RESET_COLOR
        self._low = False      # 低额度警示（百分比 / 进度条变红 + 提示语）
        self._egg = False      # 点击彩蛋：气泡只显示一条随机文案
        self._egg_text = ""    # 本次点击选中的彩蛋文案
        self._keycast_tokens: list[str] = []  # 全局按键显示：最近一次组合键键帽

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(max(1, int(config.BUBBLE_AUTOHIDE_SEC)) * 1000)
        self._hide_timer.timeout.connect(self._fade_out)

        self._keycast_timer = QTimer(self)
        self._keycast_timer.setSingleShot(True)
        self._keycast_timer.timeout.connect(self._end_keycast)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(BUBBLE_FADE_MS)
        self._fade.finished.connect(self._on_fade_finished)

        pet.attach_bubble(self)  # 接入水平镜像状态（贴图与尾迹一起翻转）

    # ------------------------------ 资源加载 ------------------------------

    def _load_pixmaps(self) -> None:
        """计算气泡卡片的尺寸与排版（不再依赖气泡贴图，直接绘制）。"""
        self._body_w = max(80, round(self._pet._pet_w * BUBBLE_WIDTH_RATIO))
        self._ui_scale = max(0.25, self._body_w / BUBBLE_UI_REF_WIDTH)
        self._body_h = max(48, round(self._body_w * BUBBLE_BODY_ASPECT))

        self._tail_w = max(10, round(self._px(BUBBLE_TAIL_W)))
        self._tail_h = max(6, round(self._px(BUBBLE_TAIL_H)))
        self._dots_w = self._tail_w
        self._dots_h = self._tail_h
        raw_dx = round(self._body_w * BUBBLE_TAIL_X_RATIO - self._tail_w / 2.0)
        self._dots_dx = min(max(10, raw_dx), self._body_w - self._tail_w - 10)
        self._dots_dx_flip = max(0, self._body_w - self._dots_dx - self._dots_w)

        self._win_w = self._body_w
        self._win_h = self._body_h + self._tail_h
        self.resize(self._win_w, self._win_h)

        normal_layout = {
            "_body_w": self._body_w, "_body_h": self._body_h,
            "_dots_w": self._dots_w, "_dots_h": self._dots_h,
            "_dots_dx": self._dots_dx, "_dots_dx_flip": self._dots_dx_flip,
            "_win_w": self._win_w, "_win_h": self._win_h,
            "_ui_scale": self._ui_scale,
            "_tail_w": self._tail_w, "_tail_h": self._tail_h,
        }

        kc = KEYCAST_BUBBLE_SCALE
        kc_w = max(60, round(self._body_w * kc))
        kc_ui = max(0.2, self._ui_scale * kc)
        kc_h = max(40, round(self._body_h * kc))
        kc_tail_w = max(8, round(self._tail_w * kc))
        kc_tail_h = max(5, round(self._tail_h * kc))
        kc_dx = round(self._dots_dx * kc)
        kc_dx = min(max(8, kc_dx), kc_w - kc_tail_w - 8)
        keycast_layout = {
            "_body_w": kc_w, "_body_h": kc_h,
            "_dots_w": kc_tail_w, "_dots_h": kc_tail_h,
            "_dots_dx": kc_dx,
            "_dots_dx_flip": max(0, kc_w - kc_dx - kc_tail_w),
            "_win_w": kc_w, "_win_h": kc_h + kc_tail_h,
            "_ui_scale": kc_ui,
            "_tail_w": kc_tail_w, "_tail_h": kc_tail_h,
        }
        self._layouts = {"normal": normal_layout, "keycast": keycast_layout}
        self._compact = False

        log.info("气泡排版：卡片 %dx%d，尾迹 %dx%d @%.2f，排版缩放 %.2fx",
                 self._body_w, self._body_h, self._dots_w, self._dots_h,
                 BUBBLE_TAIL_X_RATIO, self._ui_scale)

    def _px(self, value: float) -> float:
        """把气泡设计稿中的逻辑像素换算为当前气泡尺寸。"""
        return value * self._ui_scale

    def _pxi(self, value: float) -> int:
        return max(1, round(self._px(value)))

    def _load_fonts(self) -> None:
        families = ["MiSans", "HarmonyOS Sans SC", "微软雅黑", "Microsoft YaHei UI", "Segoe UI"]

        def mk(size: int, bold: bool = False) -> QFont:
            f = QFont()
            f.setFamilies(families)
            f.setPixelSize(size)
            f.setBold(bold)
            return f

        self._font_title = mk(self._pxi(13))
        self._font_amount = mk(self._pxi(50), True)
        self._font_amount_small = mk(self._pxi(24), True)
        self._font_sub = mk(self._pxi(13))
        self._font_delta = mk(self._pxi(12))  # 额度变化行
        self._font_egg = mk(self._pxi(CLICK_EGG_FONT_PX), True)  # 点击彩蛋大字
        self._font_key = mk(self._pxi(17), True)  # 按键显示的键帽文字
        self._font_hint = mk(self._pxi(11))

    # ------------------------------ 配置预览接口 ------------------------------

    def _apply_layout(self, compact: bool, *, reposition: bool = True) -> None:
        """切换到普通 / 按键气泡尺寸；按键模式只保留键帽。"""
        if getattr(self, "_compact", False) == bool(compact):
            return
        layout = self._layouts["keycast" if compact else "normal"]
        for name, value in layout.items():
            setattr(self, name, value)
        self._compact = bool(compact)
        if reposition and self.isVisible():
            self._reposition()
            self.update()

    def reload_layout(self) -> None:
        """按当前宠物尺寸重新计算气泡尺寸 / 字号（运行时改 PET_SCALE 后调用）。"""
        compact = bool(self._keycast_tokens)
        self._load_pixmaps()
        self._load_fonts()
        if compact:
            self._apply_layout(True, reposition=False)
        self._hide_timer.setInterval(max(1, int(config.BUBBLE_AUTOHIDE_SEC)) * 1000)
        if self.isVisible():
            self._reposition()
        self.update()

    def layout_metrics(self) -> dict:
        """气泡当前排版尺寸（配置预览用来定位 / 标注，避免重复实现排版计算）。"""
        return {
            "body_w": self._body_w,
            "body_h": self._body_h,
            "win_w": self._win_w,
            "win_h": self._win_h,
            "dots_w": self._dots_w,
            "dots_h": self._dots_h,
            "ui_scale": self._ui_scale,
            "tail_pad": round(BUBBLE_TAIL_PAD_PX * self._ui_scale),
        }

    def tail_anchor_x(self, flip_x: bool) -> float:
        """尾迹中心的 x（相对气泡窗口左边缘）。"""
        if flip_x:
            return self._body_w - self._dots_dx - self._dots_w / 2.0
        return self._dots_dx + self._dots_w / 2.0

    def set_preview_state(self, flip_x: bool, flip_down: bool = False) -> None:
        """配置预览：直接指定尾迹朝向 / 上下翻转，不依赖真实屏幕几何。"""
        self._flip_x = bool(flip_x)
        self._flip = bool(flip_down)
        self.update()

    # ------------------------------ 内容更新 ------------------------------

    def show_loading(self) -> None:
        self._percent = "--"
        self._percent_value = None
        self._sub = "正在查询套餐额度…"
        self._hint = "点击刷新"
        self._title_override = f"{config.APP_NAME} · 正在查询额度"
        self._product_name = ""
        self._source_label = ""
        self._window_label = ""
        self._reset_at = None
        self._demo = False
        self._bar_visible = True
        self._bar_value = 0.0
        self._amount_small = True
        self._amount_error = False
        self._delta_text = ""
        if self.isVisible():
            self.update()

    def show_snapshot(self, snap) -> None:
        """snap 为 quota.QuotaSnapshot（鸭子类型，避免模块耦合）。"""
        show_remaining = bool(config.QUOTA_SHOW_REMAINING)
        display_value = snap.remaining_percent if show_remaining else snap.used_percent
        self._percent = quota.format_percent(display_value)
        self._percent_value = display_value
        self._used_percent = snap.used_percent
        self._remaining_percent = snap.remaining_percent
        self._used, self._total, self._unit = snap.used, snap.total, snap.unit
        self._product_name = snap.product_name
        self._source_label = snap.source_label
        self._window_label = getattr(snap, "window_label", "")
        self._title_override = ""
        self._reset_at = snap.reset_at
        self._demo = bool(snap.demo)
        self._bar_visible = True
        self._bar_value = max(0.0, min(100.0, display_value))
        self._amount_small = False
        self._amount_error = False
        self._delta_text = ""  # 新快照先清掉旧变化行，随后由 show_change 重新设置

        parts = []
        if snap.used is not None and snap.total is not None:
            amount = f"已用 {quota.format_number(snap.used)} / {quota.format_number(snap.total)}"
            if snap.unit:
                amount += f" {snap.unit}"
            parts.append(amount)
        else:
            parts.append(f"已用 {quota.format_percent(snap.used_percent)}")
        if not show_remaining:
            parts.append(f"剩余 {quota.format_percent(snap.remaining_percent)}")
        if not snap.demo and snap.source_label:
            parts.append(snap.source_label)  # 演示标记已在标题 / 提示行，避免重复
        self._sub = " · ".join(p for p in parts if p)

        # 额度接口的时间戳统一用北京时间解析；展示时换成用户本机时区，
        # 避免桌面时钟和气泡时间对不上。
        stamp = snap.fetched_at.astimezone().strftime("%H:%M")
        now = datetime.now().astimezone()
        reset_local = snap.reset_at.astimezone() if snap.reset_at is not None else None
        if snap.demo:
            self._hint = "演示数据 · 点击刷新"
        elif reset_local is not None:
            if reset_local <= now:
                self._hint = "已到重置时间 · 点击刷新"
            elif reset_local - now < timedelta(days=1):
                self._hint = f"今日 {reset_local.strftime('%H:%M')} 重置 · {stamp} 更新"
            else:
                self._hint = f"{reset_local.strftime('%m-%d')} 重置 · {stamp} 更新"
        else:
            self._hint = f"{stamp} 更新 · 点击刷新"

        if self.isVisible():
            self.update()

    def show_error(self, kind: str, message: str) -> None:
        short = {
            "auth": "登录失效",
            "config": "未配置来源",
            "not_subscribed": "未开通",
            "quota": "未找到套餐",
            "network": "网络异常",
            "http": "服务异常",
            "parse": "数据异常",
            "cli": "arkcli 异常",
        }.get(kind, "获取失败")
        sub = message if len(message) <= 38 else message[:37] + "…"
        self._percent = short
        self._percent_value = None
        self._sub = sub
        self._hint = "订阅后点刷新" if kind == "not_subscribed" else "点击重试"
        self._title_override = f"{config.APP_NAME} · 额度状态"
        self._product_name = ""
        self._source_label = ""
        self._window_label = ""
        self._reset_at = None
        self._demo = False
        self._bar_visible = False
        self._bar_value = 0.0
        self._amount_small = True
        self._amount_error = True
        self._delta_text = ""
        if self.isVisible():
            self.update()

    def show_change(self, change) -> None:
        """展示与上次刷新之间的额度变化（change 为 quota.QuotaChange，鸭子类型）。"""
        self._delta_text = change.message
        self._delta_color = {
            "reset": DELTA_RESET_COLOR,
            "adjust": DELTA_ADJUST_COLOR,
        }.get(change.kind, DELTA_UP_COLOR)
        if self.isVisible():
            self.update()

    def set_low(self, low: bool) -> None:
        """低额度警示开关：百分比 / 进度条 / 提示语变红。"""
        self._low = low
        if self.isVisible():
            self.update()

    # ------------------------------ 全局按键显示 ------------------------------

    def show_keycast(self, tokens: list[str]) -> None:
        """显示最近一次组合键；连续按键会刷新自动收起时间。"""
        labels = []
        for token in tokens or ():
            text = str(token).strip()
            if text:
                labels.append(text)
        if not labels or getattr(self._pet, "_pressed", False):
            return
        labels = labels[:5]  # 最多四个修饰键 + 主键

        same = labels == self._keycast_tokens
        self._keycast_tokens = labels
        self._egg = False
        self._egg_text = ""
        hold_ms = max(200, round(
            float(getattr(config, "KEYCAST_AUTOHIDE_SEC", 1.2)) * 1000))
        # 相同组合键的自动重复只续时，不重新淡入 / 重绘，避免按住键时闪动。
        if not (same and self.isVisible() and self.windowOpacity() >= 0.99):
            self.present()
        self._hide_timer.stop()
        self._keycast_timer.start(hold_ms)

    def clear_keycast(self, hide: bool = True) -> None:
        """关闭按键显示：清空键帽，并默认立即收起气泡。"""
        self._keycast_timer.stop()
        had_tokens = bool(self._keycast_tokens)
        self._keycast_tokens = []
        if not had_tokens:
            return
        if hide and self.isVisible():
            self.hide_now()
        else:
            self._apply_layout(False, reposition=False)
            self.update()

    def _end_keycast(self) -> None:
        """停手超时：清空内容后淡出，下次悬停恢复额度显示。"""
        if not self._keycast_tokens:
            return
        self._keycast_tokens = []
        self._hide_timer.stop()
        if self.isVisible():
            self._fade_out()

    # ------------------------------ 显示 / 收起 ------------------------------

    def on_pet_hover(self) -> None:
        if self._pet._pressed:  # 拖拽中不弹
            return
        log.info("气泡弹出（宠物悬停）")
        self.present()

    def on_pet_unhover(self) -> None:
        if self.isVisible() and not self.underMouse():
            log.info("鼠标离开宠物，气泡将在 %d 秒后自动收起", max(1, int(config.BUBBLE_AUTOHIDE_SEC)))
            self._hide_timer.start()

    def on_pet_click(self) -> None:
        if self._egg:  # 彩蛋显示中 → 再点一次切回额度
            self._egg = False
            self._egg_text = ""
            log.info("气泡弹出（点击宠物）：切回额度显示")
        else:
            self._egg_text = random.choice(_egg_replies())
            self._egg = True
            log.info("气泡弹出（点击宠物）：显示彩蛋「%s」",
                     self._egg_text.replace("\n", " / "))
        self.present()

    def present(self) -> None:
        """淡入显示并重新计时自动收起。"""
        self._apply_layout(bool(self._keycast_tokens), reposition=False)
        self._reposition()
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self.update()  # 已可见时也立即重绘（例如点击彩蛋切换内容）
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        if self.underMouse():
            self._hide_timer.stop()
        else:
            self._hide_timer.start()

    def hide_now(self) -> None:
        self._hide_timer.stop()
        self._keycast_timer.stop()
        self._fade.stop()
        if self.isVisible():
            log.info("气泡已隐藏")
        self._egg = False
        self._egg_text = ""
        self._keycast_tokens = []
        self.hide()
        self._apply_layout(False, reposition=False)

    def follow_pet(self, dx: int, dy: int) -> None:
        """宠物漂浮时同步平移气泡，保持尾迹与头顶的相对位置不变。

        只移动窗口（DWM 合成，不重绘），每次 1~2 物理像素，开销可忽略；
        拖拽时气泡已隐藏，不会跟着满屏跑。
        """
        if self.isVisible() and (dx or dy):
            self.move(self.x() + dx, self.y() + dy)

    def _fade_out(self) -> None:
        if not self.isVisible():
            return
        log.info("气泡自动收起：开始淡出")
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if float(self._fade.endValue()) == 0.0:
            log.info("气泡已隐藏")
            self._egg = False  # 收起后彩蛋结束，下次弹出恢复额度内容
            self._egg_text = ""
            self._keycast_tokens = []
            self.hide()
            self._apply_layout(False, reposition=False)

    def wants_mirror(self, pet: "PetWindow") -> bool:
        """宠物中心是否位于当前显示器的右半边。

        是则宠物贴图与气泡尾迹一起镜像，让角色和气泡都朝屏幕内侧；
        跨到左半边后自动恢复，不再依据气泡是否会被屏幕左缘夹住来判断。
        """
        geo = (pet.screen() or QApplication.primaryScreen()).availableGeometry()
        pet_cx = pet.x() + pet.width() // 2
        center_x = geo.left() + geo.width() / 2.0
        return pet_cx > center_x

    def _reposition(self, *, do_resize: bool = True, do_flip: bool = True) -> None:
        pet = self._pet
        screen = pet.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self._win_w, self._win_h
        pet_cx = pet.x() + pet.width() // 2

        # 气泡以默认方向为基准；宠物进入显示器右半边镜像时，气泡再翻转一次。
        if do_flip:
            pet.sync_mirror()
        flip_x = pet.is_mirrored() != bool(config.BUBBLE_DEFAULT_FLIP_X)
        if flip_x != self._flip_x:
            self._flip_x = flip_x
            log.info("气泡尾迹水平翻转：%s", "朝右（默认翻转 / 显示器右侧）" if flip_x else "朝左")

        # 尾迹中心对准宠物中心（镜像时取对称位置）；两种状态各自使用独立偏移
        ratio = (config.BUBBLE_MIRROR_OFFSET_X_RATIO if self._flip_x
                 else config.BUBBLE_OFFSET_X_RATIO)
        offset = round(pet.width() * float(ratio))
        if self._flip_x:
            anchor = self._body_w - self._dots_dx - self._dots_w / 2.0
        else:
            anchor = self._dots_dx + self._dots_w / 2.0
        x = round(pet_cx - anchor + offset)
        x = min(max(x, geo.left() + 2), max(geo.left(), geo.right() - w - 2))
        log.info("气泡定位：x=%d（尾迹中心 %d，宠物中心 %d，%s，偏移 %d）",
                 x, round(x + anchor), pet_cx,
                 "镜像" if self._flip_x else "常规", offset)

        # 尾迹与头顶的间距也随气泡尺寸缩放
        tail_pad = round(BUBBLE_TAIL_PAD_PX * self._ui_scale)
        if do_flip:
            above_y = pet.y() + pet._content_top - tail_pad - h
            self._flip = above_y < geo.top() + 2  # 顶部空间不足 → 翻到宠物下方

        if not self._flip:
            y = pet.y() + pet._content_top - tail_pad - h
        else:
            y = min(pet.y() + pet._content_bottom + tail_pad,
                    max(geo.top(), geo.bottom() - h - 2))

        if do_resize:
            self.resize(w, h)
        self.move(x, y)

    # ------------------------------ 鼠标 / 绘制 ------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        log.info("鼠标进入气泡（暂停自动收起）")
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if self.isVisible():
            log.info("鼠标离开气泡（重新开始计时）")
            self._hide_timer.start()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            log.info("气泡被点击 → 请求立即刷新")
            self.clicked.emit()
            self._hide_timer.start()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform, True)
        body_y = self._dots_h if self._flip else 0
        self._draw_card(p, body_y, tail_up=self._flip)
        self._draw_texts(p, body_y)    # 文字永远正向，不随尾迹翻转

    def _tail_center_x(self) -> float:
        if self._flip_x:
            return self._dots_dx_flip + self._dots_w / 2.0
        return self._dots_dx + self._dots_w / 2.0

    def _draw_card(self, p: QPainter, body_y: int, *, tail_up: bool) -> None:
        """白卡片 + 1px 描边 + 柔和投影 + 小三角尾迹。"""
        bw, bh = self._body_w, self._body_h
        radius = self._px(BUBBLE_RADIUS)
        rect = QRectF(0.5, body_y + 0.5, bw - 1.0, bh - 1.0)

        # 投影：多层低透明度圆角矩形叠加，接近模糊阴影且不额外开滤镜
        p.setPen(Qt.NoPen)
        for i in range(10, 0, -1):
            grow = i * 1.0
            p.setBrush(QColor(*BUBBLE_SHADOW, 2))
            p.drawRoundedRect(rect.adjusted(-grow, -grow + 3.2, grow, grow + 3.2),
                              radius + grow, radius + grow)

        # 卡片
        p.setBrush(QColor(BUBBLE_CARD_COLOR))
        p.drawRoundedRect(rect, radius, radius)

        # 尾迹
        tx = self._tail_center_x()
        tw, th = float(self._dots_w), float(self._dots_h)
        if tail_up:
            base_y = body_y + 0.8
            tip_y = base_y - th
            poly = QPolygonF([QPointF(tx - tw / 2.0, base_y),
                              QPointF(tx + tw / 2.0, base_y),
                              QPointF(tx, tip_y)])
        else:
            base_y = body_y + bh - 0.8
            tip_y = base_y + th
            poly = QPolygonF([QPointF(tx - tw / 2.0, base_y),
                              QPointF(tx + tw / 2.0, base_y),
                              QPointF(tx, tip_y)])
        p.setBrush(QColor(BUBBLE_CARD_COLOR))
        p.drawPolygon(poly)

        # 描边：先描卡片，再抹掉尾迹根部那一段，最后补两条斜边
        pen = QPen(QColor(BUBBLE_BORDER_COLOR), BUBBLE_BORDER_W)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        p.setPen(QPen(QColor(BUBBLE_CARD_COLOR), BUBBLE_BORDER_W + 1.4))
        p.drawLine(QPointF(tx - tw / 2.0 + 0.8, base_y),
                   QPointF(tx + tw / 2.0 - 0.8, base_y))

        p.setPen(pen)
        p.drawLine(QPointF(tx - tw / 2.0, base_y), QPointF(tx, tip_y))
        p.drawLine(QPointF(tx, tip_y), QPointF(tx + tw / 2.0, base_y))

    def _draw_texts(self, p: QPainter, body_y: int) -> None:
        bw, bh = self._body_w, self._body_h
        pad = self._px(BUBBLE_PAD_X)

        def line(text: str, font: QFont, color: str, y_ratio: float, h: int,
                 *, center: bool = False, width: float | None = None) -> None:
            p.setFont(font)
            p.setPen(QColor(color))
            w = width if width is not None else bw - 2.0 * pad
            x = (bw - w) / 2.0 if center else pad
            p.drawText(QRectF(x, body_y + bh * y_ratio - h / 2.0, w, h),
                       (Qt.AlignHCenter if center else Qt.AlignLeft) | Qt.AlignVCenter,
                       text)

        def fitted(base: QFont, text: str, max_w: float, min_px: int) -> QFont:
            font = QFont(base)
            text_w = QFontMetrics(font).horizontalAdvance(text)
            if text_w > max_w > 0:
                font.setPixelSize(max(min_px, int(font.pixelSize() * max_w / text_w)))
            return font

        if self._keycast_tokens:  # 全局按键显示：只画键帽，气泡用紧凑尺寸
            self._draw_keycaps(p, body_y)
            return

        if self._egg:  # 点击彩蛋
            raw = (self._egg_text or "豆包豆包").replace("\r\n", "\n").replace("\r", "\n")
            lines = [item.strip() for item in raw.split("\n") if item.strip()]
            if not lines:
                lines = ["豆包豆包"]
            max_w = bw - 2.0 * pad

            if len(lines) == 1:
                # 单行彩蛋：保留卡片骨架（标题 / 标签 / 底槽 / 提示），
                # 只把主数字换成这句话——和演示片里「好的。」那张卡片一致。
                suffix = "剩余额度" if config.QUOTA_SHOW_REMAINING else "已用额度"
                title = self._title_override or _quota_title(
                    self._product_name, self._window_label,
                    bool(config.QUOTA_SHOW_REMAINING), self._demo)
                base = title
                if not self._title_override:
                    cut = title.rfind(suffix)
                    if cut > 0:
                        base = title[:cut].rstrip(" ·")
                line(self._elide(p, base or config.APP_NAME, self._font_title, max_w),
                     self._font_title, BUBBLE_TITLE_COLOR, 0.145, self._pxi(18))

                # 比主数字小一档（演示稿 430 宽下 56 : 74），过长继续缩
                egg_font = QFont(self._font_amount)
                egg_font.setPixelSize(max(self._pxi(16),
                                          round(self._font_amount.pixelSize() * 0.76)))
                egg_font = fitted(egg_font, lines[0], max_w, self._pxi(14))
                line(lines[0], egg_font, BUBBLE_INK, 0.375, self._pxi(54))

                self._draw_bar(p, body_y, 0.715, value=0.0)  # 只留底槽：这一刻它不是数字
                if not self._amount_error:  # 没有额度数据时不摆「剩余额度」标签
                    line(suffix, self._font_sub, BUBBLE_SUB_COLOR, 0.585, self._pxi(18))
                if self._hint:
                    line(self._elide(p, self._hint, self._font_hint, max_w),
                         self._font_hint, BUBBLE_HINT_COLOR, 0.885, self._pxi(16))
                return

            # 多行彩蛋：小字前情 + 大字重点，铺满整颗气泡
            setup = " ".join(lines[:-1])
            punch = lines[-1]
            setup_font = QFont(self._font_egg)
            setup_font.setPixelSize(max(self._pxi(14),
                                       int(self._font_egg.pixelSize() * 0.5)))
            setup_font.setBold(False)
            setup_font = fitted(setup_font, setup, max_w * 0.94, self._pxi(14))
            punch_font = fitted(self._font_egg, punch, max_w, self._pxi(16))
            line(setup, setup_font, "#56618C", 0.32, self._pxi(40))
            line(punch, punch_font, BUBBLE_INK, 0.6, self._pxi(70))
            return

        has_delta = bool(self._delta_text)
        if has_delta:
            y_title, y_amount, y_sub = 0.125, 0.340, 0.545
            y_bar, y_delta, y_hint = 0.655, 0.755, 0.880
        else:
            y_title, y_amount, y_sub = 0.145, 0.375, 0.585
            y_bar, y_delta, y_hint = 0.715, 0.760, 0.885

        title = self._title_override or _quota_title(
            self._product_name, self._window_label,
            bool(config.QUOTA_SHOW_REMAINING), self._demo)
        max_w = bw - 2.0 * pad
        title = self._elide(p, title, self._font_title, max_w)
        line(title, self._font_title, BUBBLE_TITLE_COLOR, y_title, self._pxi(18))

        amount_font = self._font_amount_small if self._amount_small else self._font_amount
        if self._amount_error:
            amount_color = "#B23A48"
        elif self._low:
            amount_color = LOW_BALANCE_COLOR
        elif self._remaining_percent <= 30.0:
            amount_color = QUOTA_WARN_COLOR
        else:
            amount_color = BUBBLE_INK
        line(self._percent, amount_font, amount_color, y_amount, self._pxi(54))

        if self._bar_visible:
            self._draw_bar(p, body_y, y_bar)

        sub_text = self._elide(p, self._sub, self._font_sub, max_w)
        line(sub_text, self._font_sub, BUBBLE_SUB_COLOR, y_sub, self._pxi(18))
        if has_delta:
            delta_text = self._elide(p, self._delta_text, self._font_delta, max_w)
            line(delta_text, self._font_delta, self._delta_color, y_delta, self._pxi(16))

        hint_text, hint_color = self._hint, BUBBLE_HINT_COLOR
        if self._low and not self._amount_error:
            hint_text = "额度即将用完，省着点用"
            hint_color = LOW_BALANCE_COLOR
        if hint_text:
            hint_text = self._elide(p, hint_text, self._font_hint, max_w)
            line(hint_text, self._font_hint, hint_color, y_hint, self._pxi(16))

    @staticmethod
    def _elide(p: QPainter, text: str, font: QFont, max_w: float) -> str:
        """超宽时用省略号截断，保证文字不压到卡片边框。"""
        fm = QFontMetrics(font)
        if fm.horizontalAdvance(text) <= max_w:
            return text
        return fm.elidedText(text, Qt.ElideRight, int(max_w))

    def _draw_keycaps(self, p: QPainter, body_y: int) -> None:
        """只画键帽，并让键帽尽量占满气泡。

        单键 / 双键会铺得很大；组合键变长时，按宽度自动缩小，
        保证一排键帽始终完整落在气泡白底内。
        """
        labels = list(self._keycast_tokens)
        if not labels:
            return
        bw, bh = self._body_w, self._body_h
        max_w = bw * 0.90
        max_cap_h = bh * 0.66
        min_cap_h = 14.0
        # 气泡白底近似椭圆：键帽行的四个角也必须落在白底内，
        # 否则放大后的键帽会盖住外侧深色描边。
        inner_w = bw * 0.90
        inner_h = bh * 0.74

        def metrics(cap_h: float):
            font = QFont(self._font_key)
            font.setPixelSize(max(7, round(cap_h * 0.52)))
            font.setBold(True)
            fm = QFontMetrics(font)
            pad = cap_h * 0.22
            gap = max(1.0, cap_h * 0.06)
            plus_font = QFont(font)
            plus_font.setBold(False)
            plus_font.setPixelSize(max(7, round(cap_h * 0.42)))
            plus_w = max(cap_h * 0.22,
                         float(QFontMetrics(plus_font).horizontalAdvance("+")))
            widths = [max(cap_h, float(fm.horizontalAdvance(label)) + 2.0 * pad)
                      for label in labels]
            total = sum(widths) + (len(labels) - 1) * (plus_w + 2.0 * gap)
            return font, plus_font, widths, plus_w, gap, total

        cap_h = max_cap_h
        while cap_h >= min_cap_h:
            font, plus_font, widths, plus_w, gap, total = metrics(cap_h)
            fits_width = total <= max_w
            fits_card = total <= inner_w and cap_h <= inner_h
            if fits_width and fits_card:
                break
            cap_h -= 1.0
        else:
            # 极端长组合的兜底：改为一条等宽文本，保证内容不被裁掉。
            text = " + ".join(labels)
            text_font = QFont(self._font_key)
            text_font.setBold(True)
            text_font.setPixelSize(max(7, round(bh * 0.26)))
            fm = QFontMetrics(text_font)
            max_text_w = bw * 0.92
            text_w = fm.horizontalAdvance(text)
            if text_w > max_text_w > 0:
                text_font.setPixelSize(max(7, int(text_font.pixelSize()
                                                   * max_text_w / text_w)))
            p.setFont(text_font)
            p.setPen(QColor("#2C3A7A"))
            p.drawText(QRectF(bw * 0.04, body_y + bh * 0.30, bw * 0.92, bh * 0.40),
                       Qt.AlignCenter, text)
            return

        x = (bw - total) / 2.0
        y = body_y + (bh - cap_h) / 2.0
        radius = cap_h * 0.24
        border = max(1.0, cap_h * 0.03)
        shadow_dy = max(1.0, cap_h * 0.035)
        p.setRenderHint(QPainter.Antialiasing, True)

        for index, (label, width) in enumerate(zip(labels, widths)):
            shadow = QRectF(x, y + shadow_dy, width, cap_h)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(44, 58, 122, 30))
            p.drawRoundedRect(shadow, radius, radius)

            cap = QRectF(x, y, width, cap_h)
            fill = QLinearGradient(cap.topLeft(), cap.bottomLeft())
            fill.setColorAt(0.0, QColor("#FFFFFF"))
            fill.setColorAt(1.0, QColor("#E9F0FF"))
            p.setBrush(QBrush(fill))
            p.setPen(QPen(QColor("#C9D5F1"), border))
            p.drawRoundedRect(cap, radius, radius)

            # 顶部轻微高光，放大后更接近实体键帽。
            gloss = QRectF(x + border, y + border, width - 2 * border,
                           cap_h * 0.36)
            gloss_fill = QLinearGradient(gloss.topLeft(), gloss.bottomLeft())
            gloss_fill.setColorAt(0.0, QColor(255, 255, 255, 120))
            gloss_fill.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(gloss_fill))
            p.drawRoundedRect(gloss, radius * 0.8, radius * 0.8)

            p.setFont(font)
            p.setPen(QColor("#2C3A7A"))
            p.drawText(cap, Qt.AlignCenter, label)
            x += width

            if index < len(labels) - 1:
                p.setFont(plus_font)
                p.setPen(QColor("#7E89B0"))
                p.drawText(QRectF(x + gap, y, plus_w, cap_h), Qt.AlignCenter, "+")
                x += gap * 2.0 + plus_w

    def _draw_bar(self, p: QPainter, body_y: int, y_ratio: float,
                  value: float | None = None) -> None:
        """圆角进度条：底槽 + 渐变填充（低额度 / 临近用完时变红 / 橙）。

        value 为 None 时用当前额度；传 0 则只画底槽（彩蛋卡片用）。
        """
        bw = self._body_w
        bar_w = bw * QUOTA_BAR_RATIO
        bar_h = max(2.0, self._px(QUOTA_BAR_H))
        x = (bw - bar_w) / 2.0
        y = body_y + self._body_h * y_ratio - bar_h / 2.0
        radius = bar_h / 2.0

        p.setRenderHint(QPainter.Antialiasing, True)
        track = QPainterPath()
        track.addRoundedRect(QRectF(x, y, bar_w, bar_h), radius, radius)
        p.fillPath(track, QColor(QUOTA_TRACK_COLOR))

        raw_value = self._bar_value if value is None else value
        value = max(0.0, min(100.0, float(raw_value)))
        if value > 0.0:
            fill_w = max(bar_h, bar_w * value / 100.0)
            if self._low:
                gradient = QLinearGradient(x, 0, x + bar_w, 0)
                gradient.setColorAt(0.0, QColor(LOW_BALANCE_COLOR))
                gradient.setColorAt(1.0, QColor("#E86A72"))
            elif self._remaining_percent <= 30.0:
                gradient = QLinearGradient(x, 0, x + bar_w, 0)
                gradient.setColorAt(0.0, QColor(QUOTA_WARN_COLOR))
                gradient.setColorAt(1.0, QColor("#F2C46B"))
            else:
                gradient = QLinearGradient(x, 0, x + bar_w, 0)
                gradient.setColorAt(0.0, QColor(QUOTA_FILL_COLOR))
                gradient.setColorAt(1.0, QColor(QUOTA_FILL_COLOR_2))

            p.save()
            p.setClipPath(track)
            p.fillRect(QRectF(x, y, fill_w, bar_h), gradient)
            # 顶部一道很淡的高光，让进度条更有质感
            p.fillRect(QRectF(x, y + self._px(1.5), fill_w,
                              max(self._px(2.0), bar_h * 0.28)),
                       QColor(255, 255, 255, 46))
            p.restore()


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标（M4）：与宠物共用同一份右键菜单 + 发送系统通知。

    托盘不可用时静默降级（只记一条日志），气泡始终是兜底的展示通道。
    """

    def __init__(self, menu: QMenu) -> None:
        super().__init__(QIcon(str(config.BASE_DIR / config.PET_IMAGE)))
        self.setToolTip(f"{config.APP_NAME} · 额度")
        self.setContextMenu(menu)
        self._tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
        if self._tray_ok:
            self.show()
        else:
            log.warning("系统托盘不可用：提醒将只通过桌宠气泡展示")

    def set_quota_text(self, snap) -> None:
        """把当前额度摘要放进托盘悬浮提示：主窗口 + 其余窗口的已用百分比。"""
        show_remaining = bool(config.QUOTA_SHOW_REMAINING)
        value = snap.remaining_percent if show_remaining else snap.used_percent
        word = "剩余" if show_remaining else "已用"
        plan = str(getattr(snap, "product_name", "") or "").split(" · ")[0].strip()
        window = getattr(snap, "window_label", "")
        head = " ".join(part for part in (plan, f"{window}窗口" if window else "") if part)
        text = f"{config.APP_NAME} · {head} {word} {quota.format_percent(value)}"
        windows = getattr(snap, "windows", ()) or ()
        if len(windows) > 1:
            detail = " · ".join(f"{win.short} {quota.format_percent(win.used_percent)}"
                                for win in windows)
            text += f"（已用：{detail}）"
        self.setToolTip(text.strip())

    def notify(self, title: str, message: str, msec: int = 6000) -> None:
        """发送系统通知（同时写日志，方便排查与自动化测试）。"""
        log.info("通知：%s —— %s", title, message)
        if self._tray_ok:
            self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, msec)
