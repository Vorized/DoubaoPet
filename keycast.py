# -*- coding: utf-8 -*-
"""全局按键显示：用 Windows 低级键盘钩子读取最近一次组合键。

设计要点：
- 只把按键事件送进气泡显示，**不记录按键内容**，也不写日志；
- 钩子运行在独立的 QThread 消息循环里，不占用 GUI 线程；
- 只捕获 keydown（含 Alt / Win 等系统键），keyup 不产生气泡；
- 不依赖第三方库，使用 ctypes 直接调用 user32；
- 受 Windows 限制：UAC 安全桌面、以管理员权限运行的窗口通常无法捕获。
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QThread, Signal

log = logging.getLogger("pet.keycast")

IS_WINDOWS = sys.platform == "win32"

MOD_ORDER = ("ctrl", "shift", "alt", "win")
MOD_LABELS = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win"}

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_PROCESSKEY = 0xE5
VK_PACKET = 0xE7

_MODIFIER_KEYS = {
    VK_SHIFT: "shift", VK_LSHIFT: "shift", VK_RSHIFT: "shift",
    VK_CONTROL: "ctrl", VK_LCONTROL: "ctrl", VK_RCONTROL: "ctrl",
    VK_MENU: "alt", VK_LMENU: "alt", VK_RMENU: "alt",
    VK_LWIN: "win", VK_RWIN: "win",
}

_IGNORED_VKS = {0x00, VK_PROCESSKEY, VK_PACKET, 0xFF}

_SPECIAL_LABELS = {
    0x08: "Bksp",
    0x09: "Tab",
    0x0D: "Enter",
    0x13: "Pause",
    0x14: "Caps",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "PgUp",
    0x22: "PgDn",
    0x23: "End",
    0x24: "Home",
    0x25: "←",
    0x26: "↑",
    0x27: "→",
    0x28: "↓",
    0x2C: "PrtSc",
    0x2D: "Ins",
    0x2E: "Del",
    0x2F: "Help",
    0x5B: "Win",
    0x5C: "Win",
    0x5D: "Menu",
    0x5F: "Sleep",
    0x6A: "Num*",
    0x6B: "Num+",
    0x6C: "Num,",
    0x6D: "Num-",
    0x6E: "Num.",
    0x6F: "Num/",
    0x90: "NumLock",
    0x91: "Scroll",
    0xAD: "Mute",
    0xAE: "Vol-",
    0xAF: "Vol+",
    0xB0: "Next",
    0xB1: "Prev",
    0xB2: "Stop",
    0xB3: "Play",
    0xB4: "Mail",
    0xB5: "Media",
}

_OEM_LABELS = {
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
    0xE2: "\\",
}

if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _LRESULT = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _LPARAM = ctypes.c_ssize_t
    _HHOOK = ctypes.c_void_p
    _HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, _WPARAM, _LPARAM)

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, _HOOKPROC, ctypes.c_void_p, wintypes.DWORD,
    ]
    _user32.SetWindowsHookExW.restype = _HHOOK
    _user32.UnhookWindowsHookEx.argtypes = [_HHOOK]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [_HHOOK, ctypes.c_int, _WPARAM, _LPARAM]
    _user32.CallNextHookEx.restype = _LRESULT
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT,
    ]
    _user32.GetMessageW.restype = ctypes.c_int
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
        wintypes.UINT, wintypes.UINT,
    ]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, _WPARAM, _LPARAM,
    ]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.GetKeyNameTextW.argtypes = [ctypes.c_long, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetKeyNameTextW.restype = ctypes.c_int
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = ctypes.c_void_p
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    _WH_KEYBOARD_LL = 13
    _HC_ACTION = 0
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _WM_QUIT = 0x0012
    _LLKHF_EXTENDED = 0x01


def _key_name_text(vk: int, scan_code: int, extended: bool) -> str:
    """对未知按键使用系统键名；查不到时返回空串。"""
    if not IS_WINDOWS:
        return ""
    scan = int(scan_code) or int(_user32.MapVirtualKeyW(vk, 0))
    if not scan:
        return ""
    lparam = (scan << 16) | ((1 << 24) if extended else 0)
    buf = ctypes.create_unicode_buffer(64)
    if _user32.GetKeyNameTextW(lparam, buf, len(buf)):
        return buf.value.strip()
    return ""


def key_label(vk: int, scan_code: int = 0, extended: bool = False) -> str:
    """把一个 Windows 虚拟键码转成适合键帽显示的短标签。"""
    vk = int(vk)
    if 0x41 <= vk <= 0x5A:       # A-Z
        return chr(vk)
    if 0x30 <= vk <= 0x39:       # 0-9
        return chr(vk)
    if 0x60 <= vk <= 0x69:       # 小键盘 0-9
        return f"Num{vk - 0x60}"
    if 0x70 <= vk <= 0x87:       # F1-F24
        return f"F{vk - 0x6F}"
    if vk in _SPECIAL_LABELS:
        return _SPECIAL_LABELS[vk]
    if vk in _OEM_LABELS:
        return _OEM_LABELS[vk]
    name = _key_name_text(vk, scan_code, extended)
    if name:
        return name
    return f"0x{vk:02X}"


def tokens_for(vk: int, modifiers: set[str] | None = None,
               scan_code: int = 0, extended: bool = False) -> list[str]:
    """把一次按键整理成键帽列表，例如 ["Ctrl", "Shift", "S"]。

    修饰键本身也会显示（例如单独按 Ctrl 显示 Ctrl）；IME 处理键忽略。
    """
    vk = int(vk)
    if vk in _IGNORED_VKS:
        return []
    mods = set(modifiers or ())
    kind = _MODIFIER_KEYS.get(vk)
    if kind is not None:
        mods.add(kind)
        return [MOD_LABELS[key] for key in MOD_ORDER if key in mods]
    label = key_label(vk, scan_code, extended)
    if not label:
        return []
    return [MOD_LABELS[key] for key in MOD_ORDER if key in mods] + [label]


def active_modifiers() -> set[str]:
    """读取当前全局修饰键状态（在钩子线程中调用）。"""
    if not IS_WINDOWS:
        return set()
    mods: set[str] = set()

    def down(vk: int) -> bool:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

    if down(VK_CONTROL) or down(VK_LCONTROL) or down(VK_RCONTROL):
        mods.add("ctrl")
    if down(VK_SHIFT) or down(VK_LSHIFT) or down(VK_RSHIFT):
        mods.add("shift")
    if down(VK_MENU) or down(VK_LMENU) or down(VK_RMENU):
        mods.add("alt")
    if down(VK_LWIN) or down(VK_RWIN):
        mods.add("win")
    return mods


class KeycastListener(QThread):
    """后台全局键盘监听线程。

    信号：
    - combo_pressed(list[str])：最近一次组合键的键帽标签，例如 ["Ctrl", "C"]；
    - failed(str)：钩子安装失败 / 线程异常。
    """

    combo_pressed = Signal(object)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._hook = None
        self._thread_id = 0
        self._stop_requested = False
        self._callback_error_logged = False
        self._hook_proc = _HOOKPROC(self._on_hook) if IS_WINDOWS else None

    # ------------------------------ 生命周期 ------------------------------

    def start(self) -> None:  # noqa: D401（QThread 接口）
        """启动监听；线程结束后可再次启动，用于菜单开关。"""
        if self.isRunning():
            return
        self._hook = None
        self._thread_id = 0
        self._stop_requested = False
        self._callback_error_logged = False
        super().start()

    def stop(self) -> None:
        """请求消息循环退出，并等待钩子卸载。"""
        self._stop_requested = True
        if not IS_WINDOWS or not self.isRunning():
            return
        thread_id = int(self._thread_id)
        if thread_id:
            _user32.PostThreadMessageW(thread_id, _WM_QUIT, 0, 0)
        if not self.wait(2000):
            log.warning("全局键盘监听线程未在 2 秒内退出，强制终止")
            self.terminate()
            self.wait(1000)

    def run(self) -> None:  # noqa: D401（QThread 接口）
        if not IS_WINDOWS:
            self.failed.emit("全局按键显示目前仅支持 Windows")
            return

        try:
            self._thread_id = int(_kernel32.GetCurrentThreadId())
            # 先创建线程消息队列，确保 stop() 的 PostThreadMessage 有队列可用。
            msg = wintypes.MSG()
            _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

            self._hook = _user32.SetWindowsHookExW(
                _WH_KEYBOARD_LL, self._hook_proc,
                _kernel32.GetModuleHandleW(None), 0,
            )
            if not self._hook:
                error = ctypes.get_last_error()
                log.error("注册全局键盘钩子失败：Windows 错误 %s", error)
                self.failed.emit(f"无法注册全局键盘钩子（Windows 错误 {error}）")
                return

            log.info("全局键盘监听已启动")
            if self._stop_requested:
                _user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)

            while True:
                result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    error = ctypes.get_last_error()
                    log.error("全局键盘消息循环异常：Windows 错误 %s", error)
                    break
                if result == 0:  # WM_QUIT
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:  # 兜底：线程异常不能静默退出
            log.exception("全局键盘监听线程异常")
            self.failed.emit(f"按键监听异常：{exc}")
        finally:
            if self._hook:
                _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._thread_id = 0
            log.info("全局键盘监听已停止")

    # ------------------------------ 钩子回调 ------------------------------

    def _on_hook(self, n_code, w_param, l_param):
        """低级键盘钩子：必须快速返回，不能拦截或修改系统按键。"""
        try:
            if (IS_WINDOWS and n_code == _HC_ACTION
                    and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN)):
                data = _KBDLLHOOKSTRUCT.from_address(int(l_param))
                tokens = tokens_for(
                    int(data.vkCode), active_modifiers(),
                    int(data.scanCode), bool(data.flags & _LLKHF_EXTENDED),
                )
                if tokens:
                    self.combo_pressed.emit(tokens)
        except Exception:
            if not self._callback_error_logged:
                log.exception("全局键盘回调异常（后续同类错误不再重复记录）")
                self._callback_error_logged = True
        return _user32.CallNextHookEx(self._hook, n_code, w_param, l_param)
