# -*- coding: utf-8 -*-
"""豆包桌宠 · 准备环境（命令行）

面向全新的 Windows：跑这一个脚本，按两下回车，环境就好了。
装完之后双击「启动桌宠.vbs」，它就出现在屏幕左下角。

它只做两件事：
  1. 建 .venv 并装依赖（已经装过就跳过）
  2. 问你额度从哪来（默认：先用演示数据）

不联网取数据、不上传任何东西；除了装依赖和写你自己的环境变量，不改系统。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"

# ---- Node / arkcli ----
# 目标是这一步不需要用户输入任何命令：缺 Node 就自己下载、自己解压到当前用户的目录，
# 再用它装 arkcli。全程不需要管理员权限，也不改 PATH。
_local = os.environ.get("LOCALAPPDATA")
NODE_HOME = (Path(_local) / "DoubaoPet" / "node") if _local else (ROOT / ".node")
NPM_GLOBAL = Path(os.environ.get("APPDATA") or "") / "npm"
NODE_DIST = "https://nodejs.org/dist/latest-v22.x/"

# 输出到真正的控制台时，Python 走的是 Unicode 通道（内部 UTF-16），中文一定正确，
# 所以这里不动它——码页和字体由 bootstrap.ps1 负责。
# 只有被重定向到文件/管道时才需要挑编码：默认会退到 cp1252，中文直接抛 UnicodeEncodeError。
def _fix_stdout() -> None:
    try:
        if sys.stdout.isatty():
            return
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_fix_stdout()

BAR = "=" * 56
THIN = "-" * 56


def entry_bat() -> str:
    """入口 .bat 的文件名。

    这个名字被改过（新手引导.bat → 第一次使用先运行我.bat），以后也可能再改，
    所以按目录里实际存在的取，不写死——否则提示会指向一个不存在的文件。
    """
    hits = sorted(p.name for p in ROOT.glob("*.bat"))
    return hits[0] if hits else "第一次使用先运行我.bat"


def line(text: str = "") -> None:
    print(text, flush=True)


def step(n: int, total: int, title: str) -> None:
    line()
    line(f"[{n}/{total}] {title}")
    line(THIN)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix} ").strip()
    except (EOFError, KeyboardInterrupt):
        line()
        raise SystemExit(0)
    return value or default


def run(args: list[str], **kwargs) -> int:
    return subprocess.call(args, cwd=str(ROOT), **kwargs)


def python_ok() -> bool:
    return sys.version_info >= (3, 9)


def deps_installed() -> bool:
    if not VENV_PY.exists():
        return False
    # 不能只判断文件在不在：Python 换过版本、或 .venv 被搬过之后，
    # 里面的 python.exe 会变成「不是有效的 Win32 应用」，subprocess 直接抛 OSError。
    try:
        return subprocess.call(
            [str(VENV_PY), "-c", "import PySide6, requests"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ) == 0
    except Exception:
        return False


def venv_ok() -> bool:
    """已有的 .venv 还能用吗——Python 升级 / 换版本之后常常不能。"""
    if not VENV_PY.exists():
        return False
    try:
        return subprocess.call([str(VENV_PY), "-c", "import sys"],
                               cwd=str(ROOT),
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               timeout=30) == 0
    except Exception:
        return False


def make_venv() -> bool:
    line("  正在建一个独立的运行环境（.venv），大约 20 秒…")
    code = run([sys.executable, "-m", "venv", str(VENV)])
    if code != 0 or not VENV_PY.exists():
        line("  [失败] 建不出来。请确认 Python 是完整安装（不是精简版）。")
        return False
    line("  [OK] 环境建好了")
    return True


def install_deps() -> bool:
    line("  正在装依赖（PySide6、requests）。第一次大约 1 分钟，看进度不用管…")
    line()
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip", "-q",
         "--disable-pip-version-check"])
    code = run([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt",
                "--disable-pip-version-check"])
    line()
    if code == 0:
        line("  [OK] 依赖装好了")
        return True

    # 不再打印「请手动执行 xxx」——普通用户不会去复制命令。直接问一句，自动换镜像重试。
    line("  [!] 直连 PyPI 没成功，多半是网络慢或被挡。")
    if not ask("  换用国内镜像再试一次？[Y/n]", "y").lower().startswith("n"):
        line()
        line("  正在用清华镜像重试…")
        line()
        code = run([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt",
                    "--disable-pip-version-check",
                    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
        line()
        if code == 0:
            line("  [OK] 依赖装好了（走镜像）")
            return True

    line("  [失败] 依赖没装上。网络恢复后重新双击「" + entry_bat() + "」就行，")
    line("         已经装好的部分不会重复装。")
    return False


def find_arkcli() -> str | None:
    """PATH 里没有时，再按 npm 的默认安装位置找一次。

    quota.py 里也有一份同样思路的兜底查找——所以只要装到 %APPDATA%\\npm，
    以后双击启动也能被认到，不需要改 PATH。
    """
    found = shutil.which("arkcli")
    if found:
        return found
    for name in ("arkcli.cmd", "arkcli.exe", "arkcli.bat"):
        candidate = NPM_GLOBAL / name
        if candidate.is_file():
            return str(candidate)
    return None


def find_node() -> tuple[str, Path] | tuple[None, None]:
    exe = shutil.which("node")
    if exe:
        return exe, Path(exe).parent
    if NODE_HOME.is_dir():
        hits = sorted(NODE_HOME.rglob("node.exe"))
        if hits:
            return str(hits[-1]), hits[-1].parent  # 名字排在最后的版本号最大
    return None, None


def _fetch(url: str, timeout: int = 60):
    request = urllib.request.Request(url, headers={"User-Agent": "DoubaoPet-Setup"})
    return urllib.request.urlopen(request, timeout=timeout)


def download_node() -> bool:
    """下载 Node.js 的免安装版并解压到当前用户目录；不需要管理员，也不改 PATH。"""
    line("        正在查 Node.js 的最新版本…")
    try:
        sums = _fetch(NODE_DIST + "SHASUMS256.txt", timeout=30).read().decode("utf-8", "replace")
    except Exception as exc:
        line(f"  [失败] 连不上 nodejs.org：{exc}")
        line("         在公司网络里的话，先设好 HTTP_PROXY / HTTPS_PROXY 再重跑。")
        return False

    name = want = ""
    for row in sums.splitlines():
        parts = row.split()
        if len(parts) == 2 and parts[1].endswith("-win-x64.zip"):
            want, name = parts[0], parts[1]
            break
    if not name:
        line("  [失败] 版本清单里没有 Windows 版，稍后重跑一次。")
        return False

    NODE_HOME.mkdir(parents=True, exist_ok=True)
    zip_path = NODE_HOME / name
    tty = sys.stdout.isatty()
    try:
        with _fetch(NODE_DIST + name, timeout=180) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(zip_path, "wb") as fh:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if tty and total:
                        print(f"\r        下载 Node.js {done * 100 // total:3d}%  "
                              f"{done / 1048576:5.1f} / {total / 1048576:.1f} MB",
                              end="", flush=True)
        if tty:
            print()
    except Exception as exc:
        line(f"  [失败] 下载中断：{exc}")
        return False

    if hashlib.sha256(zip_path.read_bytes()).hexdigest().lower() != want.lower():
        line("  [失败] 文件校验不通过（下载坏了），重跑一次即可。")
        return False

    line("        解压…")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(NODE_HOME)
    except Exception as exc:
        line(f"  [失败] 解压失败：{exc}")
        return False
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    line("  [OK] Node.js 就位")
    return True


def install_arkcli() -> bool:
    node, node_dir = find_node()
    if node:
        line()
        line("  [1/2] 已经有 Node.js，跳过")
    else:
        line()
        line("  [1/2] 这台电脑没有 Node.js，先下一个（只装给你自己）")
        if not download_node():
            return False
        node, node_dir = find_node()
        if not node:
            line("  [失败] 装完了却找不到 node.exe，重跑一次即可。")
            return False

    npm_cli = Path(node_dir) / "node_modules" / "npm" / "bin" / "npm-cli.js"
    if not npm_cli.is_file():
        line("  [失败] 这个 Node.js 里没有带 npm，没法继续。")
        return False

    line()
    line("  [2/2] 正在装 arkcli（npm 下载，约 20 秒）…")
    line()
    env = dict(os.environ)
    env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    # 必须显式指定全局目录。免安装版 Node 的 npm 会把 -g 装到 node.exe 旁边，
    # 那个位置既不在 PATH 也不在 %APPDATA%\npm —— 装了等于没装：
    # 这里找不到，quota.py 的兜底查找也找不到。
    env["npm_config_prefix"] = str(NPM_GLOBAL)
    # 用 node 直接跑 npm-cli.js，绕开 .cmd —— 免得路径里有空格时被 cmd 的引号规则坑到。
    # 包名是 @volcengine/ark-cli（带连字符），装出来的命令才叫 arkcli。
    # 写成 @volcengine/arkcli 会在 npm 上 404 —— 这里踩过一次。
    code = subprocess.call([node, str(npm_cli), "install", "-g", "@volcengine/ark-cli"],
                           cwd=str(ROOT), env=env)
    line()
    if code != 0:
        line("  [失败] npm 没能装上 arkcli（多半是网络）。")
        return False
    return True


def arkcli_logged_in(ark: str) -> bool | None:
    """True=已登录，False=没登录，None=问不出来。"""
    try:
        proc = subprocess.run([ark, "auth", "status"], capture_output=True, text=True,
                              errors="replace", timeout=30)
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return None
    return str(data.get("auth_method") or "none").lower() != "none"


def arkcli_login(ark: str) -> bool:
    """唤起登录：arkcli 会打开浏览器，这里等它回来（最多 3 分钟）。"""
    line()
    line("  正在打开登录页（在浏览器里完成就行，这个窗口会等着）…")
    line()
    try:
        code = subprocess.call([ark, "auth", "login", "volc-sso"],
                               cwd=str(ROOT), timeout=180)
    except subprocess.TimeoutExpired:
        line()
        line("  [!] 等了 3 分钟没结果，先跳过。")
        return False
    except KeyboardInterrupt:
        line()
        line("  [!] 已取消登录。")
        return False
    line()
    return code == 0


def choose_source() -> None:
    # 选项写成「动作」而不是「声明」：新机器上没人能自称装过 arkcli。
    # 装没装由这里探一次，装了就补一句，没装就直接问要不要现在装。
    ark = find_arkcli()
    line("  它显示的那个百分比，从哪来？三选一：")
    line()
    line("    1) 安装依赖 arkcli —— 用火山账号读真实额度")
    if ark:
        line("       （这台电脑已经装了，选 1 就能直接用）")
    line("    2) 我有 AK/SK  —— 直接连火山接口")
    line("    3) 先看看样子  —— 用自带的演示数据，不用账号（推荐）")
    line()
    # 默认落在 3：一路按回车的人应该走到「能直接跑」的那条路，而不是撞在没装的东西上。
    choice = ask("  选哪个？", "3")

    if choice.startswith("1"):
        if ark:
            line(f"  [OK] 检测到已安装：{ark}")
        else:
            line()
            line("  这台电脑还没装 arkcli。要现在装吗？")
            line()
            line("    它依赖 Node.js，我会一起装好，全程不需要你输入任何命令：")
            line("      · 下到本机用户目录（约 30 MB），不动系统、不需要管理员")
            line("      · 大约 2–3 分钟")
            line()
            if ask("  现在装？[Y/n]", "y").lower().startswith("n"):
                line()
                line("  [OK] 那先用演示数据。以后想换：重跑一次选 1。")
                return
            if not install_arkcli():
                line()
                line("  [OK] 这次先用演示数据。网络好了重跑一次，选 1 再试。")
                return
            ark = find_arkcli()
            line(f"  [OK] arkcli 装好了：{ark or ''}")

        # 装好不等于能用：没登录时桌宠只会显示「未登录」，等于白装。
        # 所以这里接着把登录也办掉，默认是（回车就去浏览器）。
        if ark and arkcli_logged_in(ark):
            line("      已经登录过，直接就能读真实额度。")
            return

        line()
        line("      还差一步：用火山账号登录一次，才能读到真实额度。")
        line()
        if ask("  现在登录？（会打开浏览器）[Y/n]", "y").lower().startswith("n"):
            line("  [OK] 稍后可以在桌宠里登录：右键 →「连接额度 / 登录」。")
            return

        if ark and arkcli_login(ark) and arkcli_logged_in(ark):
            line("  [OK] 登录完成，启动桌宠就能看到真实额度。")
            return
        line("  [!] 没确认到登录完成。稍后右键桌宠 →「连接额度 / 登录」再试。")
        return

    if choice.startswith("2"):
        line()
        line("  这两位是火山的访问密钥。注意：Windows 会把它存在你自己的用户环境变量里，")
        line("  明文，只在这台电脑生效。不想存就按 Ctrl+C 退出，改用 arkcli。")
        line()
        ak = ask("  AccessKey：")
        sk = ask("  SecretKey：")
        if ak and sk:
            subprocess.call(["setx", "VOLC_ACCESS_KEY", ak], stdout=subprocess.DEVNULL)
            subprocess.call(["setx", "VOLC_SECRET_KEY", sk], stdout=subprocess.DEVNULL)
            line("  [OK] 存好了（下次启动生效）")
            return
        line("  [!] 没填完整，先用演示数据。")
        return

    line("  [OK] 用演示数据：打开就能看到 62.6%，不需要任何账号。")


def main() -> int:
    line()
    line(BAR)
    line("  豆包桌宠 · 准备环境")
    line(BAR)
    line()
    line("  这是一个住在桌面上的小东西：不用打开，它一直在。")
    line("  鼠标移上去，它就告诉你还剩多少额度。")
    line()
    line("  接下来只做两件事：装好它要用的库，问一句额度从哪来。")
    line("  一路按回车就行，全程约一分钟。")

    if os.name != "nt":
        line()
        line("  [!] 这个桌宠目前只有 Windows 版。")
        return 1
    if not python_ok():
        line()
        line(f"  [!] Python {sys.version.split()[0]} 太旧了，需要 3.9 以上。")
        return 1

    step(1, 3, "准备运行环境")
    if deps_installed():
        line("  [OK] 之前装过，跳过")
    else:
        if not venv_ok():
            if VENV.exists():
                line("  [!] 原来那个环境用不了（多半是 Python 换过版本），重建一个。")
                shutil.rmtree(VENV, ignore_errors=True)
            if not make_venv():
                return 1
        if not install_deps():
            return 1

    step(2, 3, "额度从哪来")
    choose_source()

    step(3, 3, "完成")
    line("  环境好了。以后想让它出来，双击这个文件：")
    line()
    line("      启动桌宠.vbs")
    line()
    line("  它会在屏幕左下角出现，个子很小。")
    line("    · 鼠标移上去   弹额度")
    line("    · 左键戳一下   说胡话")
    line("    · 右键         菜单（「退出」也在里面）")
    line("    · 拖到屏幕右半边会自动翻面")
    line()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        line("\n  已取消。")
        raise SystemExit(0)
