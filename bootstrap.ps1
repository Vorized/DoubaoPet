# 豆包桌宠 · 环境配置（面向全新的 Windows）
#
# 它只做三件事：
#   1. 找 Python；没有就静默装一个（winget 优先，失败则用 python.org 官方安装包）
#   2. 交给 guide.py：建 .venv、装依赖、问一句额度从哪来
#   3. 告诉你下一步该点哪个文件
#
# 不写注册表自启动、不改系统设置、不装驱动。

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root
try { $Host.UI.RawUI.WindowTitle = "豆包桌宠 · 环境配置" } catch { }

# ------------------------------------------------------------------
# 让中文在这个窗口里显示得出来
#
# 中文 Windows（ACP 936）本来就显示正常——这种情况下什么都不碰，用系统原样。
#
# 出问题的是英文版 Windows：控制台默认码页 437、默认字体 Consolas，两个都没有中文字形，
# 于是每个汉字都落成「?」。只改码页不够：conhost 只在 DBCS 码页下才走字体链接，
# 而这台机器的 Consolas 根本没有 SystemLink 条目。
# 所以只在非中文系统上补两件事：码页切到 936，字体换成 NSimSun（新宋体，等宽，控制台可用）。
# ------------------------------------------------------------------
function Set-CJKConsoleFont {
    try {
        if (-not ("ConFont" -as [type])) {
            Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ConFont {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CONSOLE_FONT_INFO_EX {
        public uint cbSize; public uint nFont;
        public short dwFontSizeX; public short dwFontSizeY;
        public uint FontFamily; public uint FontWeight;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string FaceName;
    }
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetCurrentConsoleFontEx(IntPtr h, bool bMax, ref CONSOLE_FONT_INFO_EX f);
    public static bool Set(string face, short w, short h) {
        var f = new CONSOLE_FONT_INFO_EX();
        f.cbSize = (uint)Marshal.SizeOf(typeof(CONSOLE_FONT_INFO_EX));
        f.nFont = 0; f.dwFontSizeX = w; f.dwFontSizeY = h;
        f.FontFamily = 54; f.FontWeight = 400; f.FaceName = face;
        return SetCurrentConsoleFontEx(GetStdHandle(-11), false, ref f);
    }
}
"@ -ErrorAction Stop
        }
    } catch { return "" }

    foreach ($face in @("NSimSun", "SimSun", "SimSun-ExtB", "MS Gothic")) {
        foreach ($h in @(16, 18, 20, 24)) {
            try { if ([ConFont]::Set($face, 8, [short]$h)) { return $face } } catch { }
        }
    }
    return ""
}

# 中文 / 日文 / 韩文 Windows 原样不动：它们的控制台本来就有字形，改了只会动到人家的字体设置。
$acp = 0
try { $acp = [int](Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage" -Name ACP).ACP } catch { }
$cjkSystem = @(936, 950, 932, 949) -contains $acp

$cjkFont = "native"
if (-not $cjkSystem) {
    try { [Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(936) } catch { }
    $cjkFont = ""
    if (-not [Console]::IsOutputRedirected) { $cjkFont = Set-CJKConsoleFont }
}

function Line($t = "") { Write-Host $t }
function Step($n, $t) { Line ""; Line "[$n/3] $t"; Line ("-" * 56) }

# ------------------------------------------------------------------
# 开场 + 回车确认
#   装东西之前先停下来，让人知道接下来会发生什么，也让误双击可以退出。
# ------------------------------------------------------------------
Line ""
Line ("=" * 56)
Line "  豆包桌宠 · 环境配置"
Line ("=" * 56)
Line ""
Line "  这是一个住在桌面上的小东西：不用打开，它一直在。"
Line "  鼠标移上去，它就告诉你还剩多少额度。"
Line ""
Line "  接下来会检查这台电脑，缺什么装什么，然后问一句额度从哪来。"
Line "  大约一分钟。中途别关窗口。"
Line ""
Line ("  " + ("-" * 52))
Line "  按 Enter 开始配置；直接关掉这个窗口就是取消。"
Line ("  " + ("-" * 52))
Line ""
$null = Read-Host "  按 Enter 开始"

if (-not $cjkSystem -and -not $cjkFont -and -not [Console]::IsOutputRedirected) {
    Line ""
    Line "  [note] Could not switch the console font. If Chinese text shows as '?',"
    Line "         right-click the window title -> Properties -> Font -> choose NSimSun."
}

function Get-PythonCmd {
    # 返回 @{ Exe = "..."; Pre = @(...) }，找不到就 $null
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $ok = & py -3 -c "import sys; print(1 if sys.version_info >= (3,9) else 0)" 2>$null
            if ("$ok".Trim() -eq "1") { return @{ Exe = "py"; Pre = @("-3") } }
        } catch { }
    }
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) {
        try {
            $ok = & python -c "import sys; print(1 if sys.version_info >= (3,9) else 0)" 2>$null
            if ("$ok".Trim() -eq "1") { return @{ Exe = "python"; Pre = @() } }
        } catch { }
    }
    # 刚装完时 PATH 还没刷新，直接去常见位置找
    $guesses = @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "C:\Python313\python.exe", "C:\Python312\python.exe", "C:\Python311\python.exe"
    )
    foreach ($g in $guesses) { if (Test-Path $g) { return @{ Exe = $g; Pre = @() } } }
    return $null
}

function Save-PythonInstaller {
    # 源顺序：国内镜像优先。官方 python.org 在国内经常几十 KB/s，甚至直接连不上
    # （实测有一次 84 秒后 "Unable to connect"），镜像快一个数量级。
    # 因为从第三方镜像取 exe，所以每个文件都要对 SHA256 —— 哈希是官方安装包的，
    # 两个镜像各自独立返回同一个值。对不上就跳过这个源，不放行。
    param([string]$Dest)
    $expected = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
    $urls = @(
        "https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-amd64.exe",
        "https://registry.npmmirror.com/-/binary/python/3.12.10/python-3.12.10-amd64.exe",
        "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
    )
    foreach ($u in $urls) {
        try {
            Invoke-WebRequest -Uri $u -OutFile $Dest -UseBasicParsing -TimeoutSec 900
            $size = (Get-Item $Dest).Length
            if ($size -lt 20MB) { throw "文件不完整（$([math]::Round($size/1MB,1)) MB）" }
            $hash = (Get-FileHash -Path $Dest -Algorithm SHA256).Hash.ToLower()
            if ($hash -ne $expected) { throw "校验不通过（得到 $($hash.Substring(0,12))…）" }
            return $true
        } catch {
            Line "  这个源不行（$($_.Exception.Message.Split([Environment]::NewLine)[0])），换下一个…"
        }
    }
    return $false
}

function Install-Python {
    # 官方安装包放前面：实测 40 秒、退出码 0、InstallAllUsers=0 不需要管理员，
    # 装完正好落在下面 Get-PythonCmd 的猜测路径里。
    # winget 走机器级安装，多半会弹 UAC —— 放在后面，只在下载/安装失败时兜底。
    $tmp = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
    Line "  正在下载 Python（约 26 MB）…"
    if (Save-PythonInstaller -Dest $tmp) {
        Line "  正在安装（静默，只装给当前用户，不需要管理员）…"
        try {
            $proc = Start-Process -FilePath $tmp -Wait -PassThru -ArgumentList `
                "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_pip=1", "Include_test=0"
            if ($proc.ExitCode -eq 0) { return $true }
            Line "  官方安装包没装上（退出码 $($proc.ExitCode)），改用 winget。"
        } catch {
            Line "  安装器没跑起来，改用 winget。"
        }
    } else {
        Line "  三个源都没下下来，改用 winget。"
    }

    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $wg) { return $false }
    Line "  正在用 winget 安装 Python 3.12（1–2 分钟，别关窗口）…"
    try {
        & winget install --id Python.Python.3.12 -e --source winget --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

# ---------------- 1. Python ----------------
Step 1 "Python"
$py = Get-PythonCmd
if ($py) {
    $ver = & $py.Exe @($py.Pre + @("-c", "import platform;print(platform.python_version())"))
    Line "  [OK] 已经有 Python $("$ver".Trim())"
} else {
    Line "  这台电脑还没有 Python。现在装一个，只装给当前用户，不动系统。"
    Line ""
    if (-not (Install-Python)) {
        Line ""
        Line "  [失败] 没装上。可以手动来一次："
        Line "         1) 打开 https://www.python.org/downloads/"
        Line "         2) 下载安装，务必勾选 Add python.exe to PATH"
        Line "         3) 装完重新双击本文件"
        Line ""
        Read-Host "  按 Enter 关闭"
        exit 1
    }
    $py = Get-PythonCmd
    if (-not $py) {
        Line ""
        Line "  [失败] 装完了但没找到 python.exe。请重启电脑后再双击本文件。"
        Read-Host "  按 Enter 关闭"
        exit 1
    }
    Line "  [OK] Python 装好了"
}

# ---------------- 2. 运行环境 ----------------
Step 2 "运行环境"
$exe = $py.Exe
$pre = $py.Pre
& $exe @($pre + @("guide.py"))
if ($LASTEXITCODE -ne 0) {
    Line ""
    Line "  配置没走完。把上面的提示看完，改好之后重新双击本文件即可。"
    Read-Host "  按 Enter 关闭"
    exit $LASTEXITCODE
}

# ---------------- 3. 完成 ----------------
Step 3 "完成"
Line "  环境好了。以后想让它出来，双击这个文件："
Line ""
Line "      启动桌宠.vbs"
Line ""
Line "  它会在屏幕左下角出现，个子很小。"
Line "    · 鼠标移上去   弹额度"
Line "    · 左键戳一下   说胡话"
Line "    · 右键         菜单（「退出」也在里面）"
Line "    · 拖到屏幕右半边会自动翻面"
Line ""
Line "  现在可以把窗口关掉了。"
Line ""
$null = Read-Host "  按 Enter 关闭"
