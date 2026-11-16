# -*- coding: utf-8 -*-
"""豆包桌宠 —— 程序入口。

透明置顶宠物 + 漂浮 / 呼吸动画 + 拖拽 + 单实例；
额度气泡（剩余百分比 + 进度条）、额度消耗 / 重置提示、低额度提醒、系统托盘。
运行方式：
    .venv\\Scripts\\pythonw.exe main.py     （推荐：无控制台窗口）
    或双击“启动桌宠.vbs”
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import (QDir, QLockFile, QObject, Qt, QTimer, QUrl, Signal,
                            Slot)
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

import config
import quota
from config_preview import ConfigPreviewDialog
from keycast import KeycastListener
from ui import BubbleWindow, PetWindow, TrayIcon

log = logging.getLogger("pet.main")

IPC_NAME = "DoubaoPet.IPC.v1"
LOCK_FILE = "doubao-pet-single-instance.lock"


def setup_logging() -> None:
    level = getattr(logging, str(config.LOG_LEVEL).upper(), logging.INFO)
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # pythonw 启动时没有 stderr，就只写日志文件
        handlers.append(logging.StreamHandler())
    try:
        log_dir = Path(os.environ.get("LOCALAPPDATA", str(config.BASE_DIR))) / "DoubaoPet"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "pet.log", maxBytes=256 * 1024, backupCount=2, encoding="utf-8")
        handlers.append(fh)
    except OSError:
        pass
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def notify_existing_instance() -> bool:
    """若有实例在运行，通知它把桌宠抬到最前；成功通知返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(IPC_NAME)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"raise")
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def start_ipc_server(pet: PetWindow) -> QLocalServer | None:
    """启动本实例的 IPC 服务，用于接收“唤起”通知。"""
    QLocalServer.removeServer(IPC_NAME)  # 清理上次异常退出可能残留的管道名
    server = QLocalServer()
    if not server.listen(IPC_NAME):
        log.warning("IPC 监听失败（不影响使用）：%s", server.errorString())
        return None

    def handle(sock: QLocalSocket) -> None:
        data = bytes(sock.readAll())
        if data:
            log.info("收到唤醒请求：%r", data)
            pet.show()
            pet.raise_()
            pet.pulse()
            sock.disconnectFromServer()

    def on_connection() -> None:
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.readyRead.connect(lambda s=sock: handle(s))

    server.newConnection.connect(on_connection)
    return server


def on_quota_change(change, pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon) -> None:
    """额度变化：重置 → 托盘通知 + 自动弹气泡；消耗 / 修正 → 仅气泡变化行。"""
    log.info("额度变化：%s", change.message)
    if change.kind == "reset":
        tray.notify("额度已重置",
                    f"新周期额度已刷新，当前剩余 {quota.format_percent(change.remaining_percent)}",
                    8000)
        bubble.show_change(change)
        if pet.isVisible():
            bubble.present()
    else:
        bubble.show_change(change)


def on_low_quota(low: bool, pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon) -> None:
    """低额度跨越阈值时提醒一次（进入红色警示 / 解除）。"""
    pet.set_low_quota(low)
    bubble.set_low(low)
    if low:
        threshold = float(config.LOW_QUOTA_REMAINING_PERCENT)
        log.warning("低额度警戒：额度剩余低于 %g%%（仅在跨越阈值时提醒一次）", threshold)
        tray.notify("豆包额度不足提醒",
                    f"当前计费窗口剩余已低于 {threshold:g}%，请合理安排使用", 10000)
        # 和「额度已重置」对称：跨越阈值也算一次需要它主动开口的时刻。
        # 不悬停也能看到红卡，否则「不足」只藏在角标和托盘里，用户多半错过。
        if pet.isVisible():
            bubble.present()
    else:
        log.info("低额度警戒解除")


def start_quota_service(pet: PetWindow, bubble: BubbleWindow,
                        tray: TrayIcon) -> quota.QuotaService:
    """装配额度服务、气泡与提醒。"""
    svc = quota.QuotaService()

    def on_fetched(snap) -> None:
        log.info("额度更新：%s", quota.format_snapshot(snap))
        tray.set_quota_text(snap)

    svc.fetched.connect(on_fetched)
    svc.failed.connect(
        lambda kind, msg, retry_in: log.warning(
            "额度获取失败[%s]：%s（%s）", kind, msg,
            "已停止自动重试，点击宠物或「立即刷新」可重试" if retry_in < 0
            else f"{retry_in} 秒后重试"))

    # 气泡内容跟随数据更新
    svc.fetched.connect(bubble.show_snapshot)
    svc.failed.connect(lambda kind, msg, retry_in: bubble.show_error(kind, msg))
    bubble.show_loading()

    # 额度变化 / 低额度提醒
    svc.changed.connect(lambda ch: on_quota_change(ch, pet, bubble, tray))
    svc.low_quota.connect(lambda low: on_low_quota(low, pet, bubble, tray))

    # 悬停 / 点击宠物 / 点击气泡 = 立即刷新一次（服务内部有防抖）
    pet.clicked.connect(svc.refresh_now)
    pet.hovered.connect(svc.refresh_now)
    bubble.clicked.connect(svc.refresh_now)

    svc.start()
    return svc


class KeycastUi(QObject):
    """把按键监听线程的信号转回主线程，更新按键气泡和菜单状态。"""

    def __init__(self, pet: PetWindow, bubble: BubbleWindow, action, tray: TrayIcon) -> None:
        super().__init__()
        self._pet = pet
        self._bubble = bubble
        self._action = action
        self._tray = tray

    @Slot(object)
    def on_combo(self, tokens) -> None:
        if self._pet.isVisible():
            self._bubble.show_keycast(list(tokens))

    @Slot(str)
    def on_failed(self, message: str) -> None:
        log.warning("按键显示不可用：%s", message)
        self._action.blockSignals(True)
        self._action.setChecked(False)
        self._action.blockSignals(False)
        self._bubble.clear_keycast()
        self._tray.notify("按键显示不可用", message, 8000)


class AuthProcessWatcher(QObject):
    """等待认证进程退出，并把退出码 / 是否超时送回 Qt 主线程。"""

    finished = Signal(int, bool)


_auth_proc: subprocess.Popen | None = None
_auth_watcher: AuthProcessWatcher | None = None


def _temp_dir() -> Path:
    path = Path(os.environ.get("TEMP") or os.environ.get("TMP")
                or str(config.BASE_DIR)) / "DoubaoPet"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _auth_log_path() -> Path:
    return _temp_dir() / "arkcli_auth.log"


def _read_auth_log(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return clean.strip()[-4000:]


def _write_auth_script(exe: str) -> Path:
    """生成可见登录窗口使用的 PowerShell 脚本（UTF-8 BOM，兼容 Windows PowerShell）。"""
    script = _temp_dir() / "connect_arkcli.ps1"
    exe_ps = exe.replace("'", "''")
    script.write_text(
        f"""$ErrorActionPreference = "Continue"
try {{ chcp 65001 > $null }} catch {{}}
try {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
}} catch {{}}
try {{ $Host.UI.RawUI.WindowTitle = "豆包桌宠 · 连接额度" }} catch {{}}

Write-Host ""
Write-Host "豆包桌宠 · 连接额度" -ForegroundColor Cyan
Write-Host "正在打开火山引擎 SSO 登录；请在浏览器中完成登录。" -ForegroundColor Gray
Write-Host ""
& '{exe_ps}' auth login volc-sso
$code = $LASTEXITCODE
if ($null -eq $code) {{ $code = 1 }}
Write-Host ""
if ($code -eq 0) {{
    Write-Host "登录成功，正在把额度连接回桌宠..." -ForegroundColor Green
    Start-Sleep -Seconds 2
}} else {{
    Write-Host "登录未完成（退出码 $code）。" -ForegroundColor Yellow
    Write-Host "可以关闭窗口，回到桌宠后重新从右键菜单连接。"
    Read-Host "按回车关闭"
}}
exit $code
""",
        encoding="utf-8-sig",
    )
    return script


def _show_auth_failure(pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon,
                       quota_svc: quota.QuotaService, exe: str, code: int,
                       timed_out: bool, timeout: int | None, detail: str,
                       visible: bool) -> None:
    """后台 / 可见登录失败时，提供重试或切换可见窗口的出口。"""
    box = QMessageBox(pet)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    box.setWindowTitle("连接豆包额度")
    box.setIcon(QMessageBox.Icon.Warning)
    if timed_out:
        box.setText(f"后台登录超过 {timeout} 秒仍未完成。")
    elif visible:
        box.setText("登录窗口已关闭，认证没有完成。")
    else:
        box.setText(f"后台登录未完成（退出码 {code}）。")
    box.setInformativeText(
        "可以重试；如果火山引擎要求手动选择项目，或者浏览器没有自动打开，"
        "请使用「可见窗口登录」。"
    )
    if detail:
        box.setDetailedText(detail)
    retry_btn = box.addButton("重试", QMessageBox.ButtonRole.AcceptRole)
    visible_btn = box.addButton("可见窗口登录", QMessageBox.ButtonRole.ActionRole)
    cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(retry_btn)
    box.setEscapeButton(cancel_btn)
    box.exec()

    clicked = box.clickedButton()
    if clicked is retry_btn:
        start_arkcli_login(pet, bubble, tray, quota_svc, exe, visible=visible)
    elif clicked is visible_btn:
        start_arkcli_login(pet, bubble, tray, quota_svc, exe, visible=True)


def _watch_auth_process(proc: subprocess.Popen, pet: PetWindow, bubble: BubbleWindow,
                        tray: TrayIcon, quota_svc: quota.QuotaService, exe: str,
                        log_path: Path | None, visible: bool,
                        timeout: int | None) -> None:
    """等待登录进程结束；成功后刷新额度，失败时给出重试 / 可见窗口选项。"""
    global _auth_proc, _auth_watcher
    _auth_proc = proc
    watcher = AuthProcessWatcher(pet)
    _auth_watcher = watcher

    def on_finished(code: int, timed_out: bool) -> None:
        global _auth_proc, _auth_watcher
        detail = _read_auth_log(log_path)
        log.info("arkcli 认证进程已退出：退出码 %s，超时=%s", code, timed_out)
        if _auth_watcher is watcher:
            _auth_watcher = None
            _auth_proc = None
        watcher.deleteLater()

        if code == 0 and not timed_out:
            quota_svc.refresh_now()
            tray.notify("豆包额度", "认证完成，正在刷新套餐额度…", 6000)
            return

        log.warning("arkcli 认证未完成：code=%s timeout=%s\n%s",
                    code, timed_out, detail[-2000:])
        quota_svc.refresh_now()
        _show_auth_failure(pet, bubble, tray, quota_svc, exe, code,
                           timed_out, timeout, detail, visible)

    watcher.finished.connect(on_finished)

    def wait_for_exit() -> None:
        timed_out = False
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            log.warning("arkcli 认证超过 %s 秒，终止后台进程", timeout)
            try:
                proc.kill()
            except OSError:
                pass
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = -1
        try:
            watcher.finished.emit(code, timed_out)
        except RuntimeError:
            pass  # 程序退出时 watcher 可能已经被销毁

    threading.Thread(target=wait_for_exit, name="arkcli-auth-wait", daemon=True).start()


def _start_hidden_auth(pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon,
                       quota_svc: quota.QuotaService, exe: str) -> None:
    """后台登录：不弹 PowerShell 窗口，只让 arkcli 打开浏览器。"""
    log_path = _auth_log_path()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            proc = subprocess.Popen(
                [exe, "auth", "login", "volc-sso"],
                cwd=str(config.BASE_DIR),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
    except OSError as exc:
        log.warning("无法启动后台认证：%s", exc)
        bubble.show_error("auth", f"无法启动后台认证：{exc}")
        bubble.present()
        return

    try:
        timeout = max(30, int(getattr(config, "ARKCLI_LOGIN_TIMEOUT_SEC", 180)))
    except (TypeError, ValueError):
        timeout = 180
    _watch_auth_process(proc, pet, bubble, tray, quota_svc, exe,
                        log_path, False, timeout)
    log.info("已启动后台 arkcli 认证：pid=%s，日志=%s", proc.pid, log_path)
    tray.notify(
        "连接豆包额度",
        "正在后台打开浏览器；完成火山引擎 SSO 登录后会自动刷新。",
        10000,
    )


def _start_visible_auth(pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon,
                        quota_svc: quota.QuotaService, exe: str) -> None:
    """可见登录窗口（排障 / 首次登录需要手动选择项目时使用）。"""
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0
    try:
        script = _write_auth_script(exe)
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            cwd=str(config.BASE_DIR),
            creationflags=creationflags,
        )
    except OSError as exc:
        log.warning("无法打开认证窗口：%s", exc)
        bubble.show_error("auth", f"无法打开认证窗口：{exc}")
        bubble.present()
        return

    _watch_auth_process(proc, pet, bubble, tray, quota_svc, exe,
                        None, True, None)
    log.info("已打开可见 arkcli 认证窗口：pid=%s，脚本=%s", proc.pid, script)
    tray.notify(
        "连接豆包额度",
        "请在弹出的登录窗口完成火山引擎 SSO 登录；完成后会自动刷新。",
        10000,
    )


def start_arkcli_login(pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon,
                       quota_svc: quota.QuotaService, exe: str,
                       visible: bool | None = None) -> None:
    """运行 `arkcli auth login volc-sso`，默认后台执行，完成后自动刷新额度。"""
    if _auth_proc is not None and _auth_proc.poll() is None:
        tray.notify("连接豆包额度", "登录流程正在进行，请先完成浏览器登录。", 6000)
        return

    use_visible = (bool(getattr(config, "ARKCLI_LOGIN_VISIBLE", False))
                   if visible is None else visible)
    if use_visible:
        _start_visible_auth(pet, bubble, tray, quota_svc, exe)
    else:
        _start_hidden_auth(pet, bubble, tray, quota_svc, exe)


def connect_quota(pet: PetWindow, bubble: BubbleWindow, tray: TrayIcon,
                  quota_svc: quota.QuotaService) -> None:
    """右键「连接额度 / 登录」：引导 arkcli SSO 认证，并连接桌宠额度。"""
    exe = quota.arkcli_executable()
    if exe is None:
        box = QMessageBox(pet)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.setWindowTitle("连接豆包额度")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("没有找到 arkcli，暂时无法登录火山引擎。")
        box.setInformativeText(
            "双击文件夹里的「第一次使用先运行我.bat」，选 1：\n"
            "它会自动装好 Node.js 和 arkcli，并带你登录一次。\n\n"
            "装完之后重启桌宠即可。"
        )
        readme_btn = box.addButton("打开安装说明", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setEscapeButton(cancel_btn)
        box.exec()
        if box.clickedButton() is readme_btn:
            try:
                os.startfile(str(config.BASE_DIR / "README.md"))
            except OSError as exc:
                log.warning("无法打开安装说明：%s", exc)
                bubble.show_error("config", f"无法打开安装说明：{exc}")
                bubble.present()
        return

    box = QMessageBox(pet)
    box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    box.setWindowTitle("连接豆包额度")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("通过火山引擎 SSO 登录，把套餐额度连接到桌宠。")
    if bool(getattr(config, "ARKCLI_LOGIN_VISIBLE", False)):
        login_desc = "点「开始登录」会打开可见登录窗口和浏览器。"
    else:
        login_desc = "点「开始登录」会在后台打开浏览器，不会弹出 PowerShell 窗口。"
    box.setInformativeText(
        login_desc + "\n"
        "在浏览器完成 SSO 登录后，桌宠会自动刷新额度。\n\n"
        "如果已经登录过，可以直接点「仅刷新额度」。\n"
        "如果浏览器没有自动打开，登录失败后可以选择「可见窗口登录」。"
    )
    login_btn = box.addButton("开始登录", QMessageBox.ButtonRole.AcceptRole)
    refresh_btn = box.addButton("仅刷新额度", QMessageBox.ButtonRole.ActionRole)
    cancel_btn = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(login_btn)
    box.setEscapeButton(cancel_btn)
    box.exec()

    clicked = box.clickedButton()
    if clicked is login_btn:
        start_arkcli_login(pet, bubble, tray, quota_svc, exe)
    elif clicked is refresh_btn:
        quota_svc.refresh_now()
        tray.notify("豆包额度", "正在刷新套餐额度…", 4000)


def main() -> int:
    setup_logging()
    os.chdir(config.BASE_DIR)  # 统一工作目录，保证相对路径可用

    app = QApplication(sys.argv)
    app.setApplicationName("DoubaoPet")
    # 宠物是 Qt.Tool 窗口；认证对话框关闭时不能把整个托盘程序一起退出。
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(str(config.BASE_DIR / config.PET_IMAGE)))

    # ---- 单实例 ----
    if notify_existing_instance():
        log.info("已有实例在运行，已唤起，本次启动退出")
        return 0

    lock = QLockFile(QDir.temp().filePath(LOCK_FILE))
    lock.setStaleLockTime(30_000)
    if not lock.tryLock(100):
        log.warning("已有实例在运行（单实例锁被占用），本次启动退出")
        return 0

    pet = PetWindow()
    pet.move_to_start()
    server = start_ipc_server(pet)
    pet.show()

    # ---- 额度气泡 ----
    bubble = BubbleWindow(pet)
    pet.hovered.connect(bubble.on_pet_hover)
    pet.unhovered.connect(bubble.on_pet_unhover)
    pet.clicked.connect(bubble.on_pet_click)
    pet.dragged.connect(bubble.hide_now)
    pet.moved.connect(bubble.follow_pet)  # 漂浮时气泡贴着头顶一起动

    # ---- 右键菜单 + 系统托盘（宠物与托盘共用同一份菜单） ----
    quota_svc: quota.QuotaService | None = None

    def do_refresh() -> None:
        if quota_svc is not None:
            quota_svc.refresh_now()

    menu = QMenu()
    act_refresh = menu.addAction("立即刷新")
    act_refresh.triggered.connect(do_refresh)

    act_open_doubao = menu.addAction("打开豆包")

    def do_open_doubao() -> None:
        url = config.DOUBAO_CHAT_URL
        if QDesktopServices.openUrl(QUrl(url)):
            log.info("已请求打开豆包：%s", url)
        else:
            log.warning("无法打开豆包：%s", url)
            tray.notify("打开豆包失败", "系统没有可用的默认浏览器。", 6000)

    act_open_doubao.triggered.connect(do_open_doubao)

    act_connect = menu.addAction("连接额度 / 登录")

    def do_connect() -> None:
        # 等菜单完全关闭后再弹对话框，避免同一次鼠标释放误触默认按钮。
        QTimer.singleShot(0, lambda: connect_quota(pet, bubble, tray, quota_svc))

    act_connect.triggered.connect(do_connect)

    act_toggle = menu.addAction("隐藏宠物")

    def toggle_pet_visible() -> None:
        if pet.isVisible():
            bubble.hide_now()
            pet.hide()
        else:
            pet.show()
            pet.raise_()

    act_toggle.triggered.connect(toggle_pet_visible)

    act_gravity = menu.addAction("重力模式")
    act_gravity.setCheckable(True)
    act_gravity.setChecked(pet.is_gravity())
    act_gravity.toggled.connect(lambda on: pet.set_mode("gravity" if on else "float"))

    act_keycast = menu.addAction("按键显示（全局）")
    act_keycast.setCheckable(True)

    menu.addSeparator()
    act_preview = menu.addAction("配置预览 / 数值调整…")
    preview_dialog: ConfigPreviewDialog | None = None

    def open_preview() -> None:
        """打开配置预览（数值填写 + 模拟显示）；已打开时只抬到最前。"""
        nonlocal preview_dialog
        if preview_dialog is not None:
            preview_dialog.show()
            preview_dialog.raise_()
            preview_dialog.activateWindow()
            return
        dialog = ConfigPreviewDialog(pet, bubble, parent=pet)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)

        def on_destroyed(*_args) -> None:
            nonlocal preview_dialog
            preview_dialog = None

        dialog.destroyed.connect(on_destroyed)
        preview_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    act_preview.triggered.connect(lambda: QTimer.singleShot(0, open_preview))

    def sync_visibility_label() -> None:
        act_toggle.setText("隐藏宠物" if pet.isVisible() else "显示宠物")
        act_gravity.setChecked(pet.is_gravity())

    menu.aboutToShow.connect(sync_visibility_label)
    menu.addSeparator()
    act_quit = menu.addAction("退出")
    pet.set_context_menu(menu)
    tray = TrayIcon(menu)

    def shutdown_ui() -> None:
        """退出前先把看得见的东西收干净。

        Windows 上有两处会「退不干净」：
        - 透明置顶窗口（宠物 / 气泡）不主动 hide 就结束进程，桌面上会留一帧残影；
        - 托盘图标不 hide，会留一枚幽灵图标，要等鼠标划过才消失。
        先收画面、再退事件循环；反过来的话窗口已经没了，残影没人擦。
        """
        nonlocal preview_dialog
        try:
            if preview_dialog is not None:
                preview_dialog.close()
                preview_dialog = None
            bubble.hide()
            pet.hide()
            tray.hide()
            QApplication.processEvents()
        except Exception as exc:  # 退出路径上不要再抛，否则窗口关不掉
            log.warning("退出清理出错：%s", exc)

    act_quit.triggered.connect(shutdown_ui)
    act_quit.triggered.connect(app.quit)
    app.aboutToQuit.connect(shutdown_ui)

    # ---- 全局按键显示 ----
    keycast = KeycastListener()
    keycast_ui = KeycastUi(pet, bubble, act_keycast, tray)

    def set_keycast_enabled(on: bool) -> None:
        if on:
            keycast.start()
            log.info("按键显示已开启：全局监听，仅显示最近组合键，不记录内容")
        else:
            keycast.stop()
            bubble.clear_keycast()
            log.info("按键显示已关闭")

    keycast.combo_pressed.connect(keycast_ui.on_combo)
    keycast.failed.connect(keycast_ui.on_failed)
    act_keycast.toggled.connect(set_keycast_enabled)
    app.aboutToQuit.connect(keycast.stop)
    if bool(getattr(config, "KEYCAST_ENABLED", False)):
        act_keycast.setChecked(True)

    # ---- 额度轮询 + 提醒接线 ----
    quota_svc = start_quota_service(pet, bubble, tray)
    app.aboutToQuit.connect(quota_svc.stop)

    log.info("%s 已启动", config.APP_NAME)
    code = app.exec()

    keycast.stop()
    quota_svc.stop()
    if not quota_svc.wait(3000):
        # 仍在跑（多半卡在 arkcli 子进程 / 网络请求上）：强制终止，避免
        # QThread 在进程退出时被析构而崩溃（QThread: Destroyed while running）。
        log.warning("额度线程 3 秒内未退出（可能正在请求远端），强制终止")
        quota_svc.terminate()
        quota_svc.wait(2000)
    if server is not None:
        server.close()
        QLocalServer.removeServer(IPC_NAME)
    log.info("退出，代码 %d", code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
