# 豆包桌宠 DoubaoPet

Windows 10/11 上的轻量豆包桌宠：透明置顶、漂浮 / 呼吸动画、拖拽、重力模式、额度气泡（**剩余百分比 + 进度条**）、低额度提醒、进入屏幕右半边自动镜像、系统托盘、**全局按键显示**，以及**配置预览（数值填写 + 模拟效果）**。

项目基于同目录的 `DeepSeekPet` 改造，把「API 余额」换成了「火山方舟套餐额度」。默认读取本地 `quota.json` 的演示数据，配置真实数据源后即可显示 Coding Plan / Agent Plan 的**多窗口**用量（Coding Plan：会话 / 周 / 月；Agent Plan：5 小时 / 周 / 月）。

> **口径**：气泡主数字取「最紧窗口」＝已用百分比最高的窗口（打平取更短的窗口），标题里标出窗口名，例如「Agent Plan · 5 小时窗口剩余额度」。真正会先卡住你的是 5 小时 / 会话窗口，只盯月额度会漏报。

> **气泡外观**：卡片式（白底、1px 描边 `#DDE3F5`、柔和投影、底部小三角尾迹），文字左对齐，层级为「标题 / 主数字 / 用量 / 进度条 / 提示」。尺寸与字号按 `BUBBLE_UI_REF_WIDTH = 320` 的设计稿等比缩放：`BUBBLE_BODY_ASPECT = 0.64`、圆角 20、内边距 20；改这些常量即可整体调形，不再依赖图片素材。

## 启动

**第一次用：双击 `第一次使用先运行我.bat`。** 它会先停一下，按 Enter 才开始配置；然后看这台电脑缺什么——没有 Python 就静默装一个（只装给当前用户），
再建运行环境、装依赖、问一句额度从哪来。选了 arkcli 那条路的话，Node.js 和 arkcli 也由它自动装好，装完还会直接唤起浏览器登录一次，全程不用你敲命令。看到「环境好了」之后，双击 **`启动桌宠.vbs`** 即可
（无控制台窗口，后台运行）。以后每次启动都不用再跑引导。

> 只想看效果、不想在电脑上装 Python：看演示视频就行，不必安装。

- 首次启动默认显示 `quota.json` 里的演示额度（已用 37.4%，剩余 62.6%）。
- 鼠标悬停宠物：弹出额度气泡；移开后约 6 秒自动收起。调整 `PET_SCALE` 时，气泡字号、行高、进度条和尾迹间距会随宠物尺寸等比缩放。
- 点击宠物：从 `config.py` 的 `CLICK_EGG_REPLIES` 中随机显示一条彩蛋文案（支持用 `\n` 换行）。**单行彩蛋**保留卡片骨架——标题、`剩余额度` 标签、空进度条、重置提示都在，只把主数字换成这句话；**多行彩蛋**则铺满整颗气泡，排成“小字前情 + 大字重点”。气泡还在时再点一次切回额度，同时触发一次刷新。
- 点击气泡：立即刷新。
- 拖动宠物：移动位置；拖到当前显示器右半边会自动水平镜像，回到左半边后恢复。
- 右键宠物或托盘图标：立即刷新 / 打开豆包 / 连接额度 / 登录 / 隐藏宠物 / 重力模式 / **按键显示（全局）** / **配置预览** / 退出。
- 托盘图标悬停可看当前窗口额度摘要（多个窗口的已用百分比都在 tooltip 里）。

> 单实例：重复双击启动器不会开出第二只，只会把已运行的桌宠抬到最前。

## 配置预览（数值填写 + 模拟显示）

右键菜单 →「配置预览 / 数值调整…」：

- 左侧每个输入框直接对应 `config.py` 里的一个配置项（缩放、漂浮幅度 / 周期、呼吸幅度、帧率、气泡偏移、自动收起、启动边距、低额度阈值），支持直接键入数值和上下微调。
- 右侧按**真实像素**模拟桌面：壁纸 + 任务栏 + 宠物 + 真实气泡。气泡用的是同一个 `BubbleWindow` 的绘制代码，字号、行高、进度条、尾迹朝向与真实运行一致。
- 拖动预览里的宠物：跨过中线会自动镜像，气泡尾迹与偏移同步换边；贴边时按真实屏幕的夹取逻辑处理。
- 上方「模拟额度」只影响预览（不写入配置），低于低额度阈值时会立刻变红并显示 `!` 角标，用来确认阈值是否合适。
- 内容大于舞台时整体等比缩小，底部会标注「预览缩放 xx%」，尺寸数字仍按真实像素显示。
- **应用到桌宠**：只在内存里生效，立刻能看到真实效果，不写文件；**保存到 config.py**：只替换对应的一行（保留注释），同目录留一份 `config.py.bak`，重启后依然生效；**恢复默认**回到出厂数值。

也可以单独打开预览窗口（不启动桌宠）：

```powershell
.venv\Scripts\python.exe config_preview.py
```

## 全局按键显示

右键菜单 →「按键显示（全局）」开启（默认关闭）：

- 在任意程序中按键，气泡会自动弹出并显示最近一次组合键，例如 `Ctrl + Shift + S`；持续输入时保持显示，停手约 1.2 秒后自动收起。
- 按键气泡内部只保留键帽（不显示标题和提示文字）；单键 / 双键会自动放大，组合键较长时按宽度等比缩小，尽量少遮挡桌面。
- 气泡收起后，下一次悬停宠物仍然显示额度；按键显示不会改动额度数据。
- 显示的是物理按键本身，不是输入法最终上屏的文字；单独按修饰键也会显示。
- 只把“最近一次组合键”用于绘制，**不记录按键内容、不写日志**。默认关闭也是出于这个考虑。
- 菜单开关只对本次运行有效；需要每次启动都开启，把 `KEYCAST_ENABLED` 改成 `True`。
- 使用 Windows 低级键盘钩子：UAC 安全桌面、以管理员权限运行的窗口通常无法捕获；宠物隐藏时不会弹出按键气泡。

## 额度数据源

在 `config.py` 的 `QUOTA_SOURCE` 中选择：

| 值 | 说明 |
| --- | --- |
| `"auto"` | 默认。依次尝试：`arkcli` → 火山 AK/SK 直连 → 本地 `quota.json` |
| `"arkcli"` | 调用 `arkcli usage plan --all --format json`（推荐，SSO 登录，不需要 AK/SK；读取全部窗口） |
| `"api"` | 用火山引擎 AK/SK 直连 OpenAPI：Coding Plan → `GetCodingPlanUsage`，Agent Plan → `GetAFPUsage`（都只支持个人版） |
| `"http"` | 从自建 / 中转额度接口拉取 JSON |
| `"file"` | 只读本地 `quota.json` |

### 推荐：arkcli

需要先安装 [Node.js](https://nodejs.org/)，然后在 PowerShell 中：

```powershell
npm i -g @volcengine/ark-cli@latest
arkcli auth login volc-sso
arkcli auth status
arkcli usage plan --all --format json
```

确认 `arkcli usage plan --all --format json` 能输出 `items` / `periods` 后，保持 `QUOTA_SOURCE = "auto"` 即可。桌宠会读取 `5h` / `weekly` / `monthly` 三个窗口，主数字取最紧窗口。

> 也可以直接右键桌宠或托盘图标 →「连接额度 / 登录」：默认在**后台**执行 `arkcli auth login volc-sso`，只打开浏览器，不弹 PowerShell / 命令行窗口。完成浏览器登录后桌宠会自动刷新额度；已登录时可在同一对话框里选择「仅刷新额度」。如果首次登录需要手动选择项目，或浏览器没有自动打开，可在 `config.py` 中设置 `ARKCLI_LOGIN_VISIBLE = True` 后重启，或直接使用失败提示里的「可见窗口登录」。

> `--all` 会同时查询个人版 / 团队版的 Coding Plan 与 Agent Plan。只订阅 Agent Plan 的账号，如果不加 `--all`，自动发现可能返回空列表；团队版没有绑定席位时会带 `error` 字段，桌宠会自动跳过。

- `QUOTA_PRODUCT = "coding-plan"`：优先 Coding Plan；没有已订阅的 Coding Plan 时自动回退到 Agent Plan（气泡标题会显示实际展示的套餐）。
- 也想优先看 Agent Plan？改成 `"agent-plan"`。
- 同一账号同时有多个套餐时只显示一个主套餐（标题里能看出是哪个）。
- 账号一个套餐都没开通时，气泡显示「未开通」而不是「剩余 100%」：`arkcli plans get` 为空时会自动判定。

### 可选：火山 AK/SK 直连

> 这里需要的是**火山引擎账号的 Access Key / Secret Key**，不是方舟推理 API Key。  
> 个人版两个套餐都支持：Coding Plan → `GetCodingPlanUsage`（只有百分比），Agent Plan → `GetAFPUsage`（带 `Quota` / `Used` / `ResetTime`，所以能显示「已用 8,750 / 35,000 点」和重置倒计时）。团队版需要 seat id，请用 arkcli。

推荐用环境变量，避免把密钥写进文件：

```powershell
[Environment]::SetEnvironmentVariable("VOLC_ACCESS_KEY", "你的 AK", "User")
[Environment]::SetEnvironmentVariable("VOLC_SECRET_KEY", "你的 SK", "User")
```

也可以在 `config.py` 填写 `VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY`。然后：

```python
QUOTA_SOURCE = "api"   # 或 "auto"（未安装 arkcli 且配置了 AK/SK 时会自动选 api）
VOLC_REGION = "cn-beijing"
```

账号需要具备查询套餐用量的权限（`ArkReadOnlyAccess` 即可，`GetCodingPlanUsage` / `GetAFPUsage` 都是只读动作）。AK/SK 只保存在本机，请勿截图、上传或发给他人。团队版不走这条直连路径，改用 `QUOTA_SOURCE = "arkcli"`。

### 可选：自定义 HTTP 接口

```python
QUOTA_SOURCE = "http"
QUOTA_HTTP_URL = "https://example.com/api/doubao/quota"
QUOTA_HTTP_TOKEN = ""          # 可选，会以 Bearer 方式发送
QUOTA_HTTP_HEADERS = {}        # 可选，额外请求头
```

接口返回扁平 JSON 即可：

```json
{
  "percent": 37.4,
  "used": 7480,
  "total": 20000,
  "unit": "次",
  "reset_at": "2026-11-01T00:00:00+08:00"
}
```

也兼容 arkcli 的 `{"items": [...]}`、火山 OpenAPI 的 `{"Result": {"QuotaUsage": [...]}}`（Coding Plan）和 `{"Result": {"AFPMonthly": {...}}}`（Agent Plan）完整结构。`percent` 表示**已用百分比**，不是剩余百分比。

### 本地文件（默认演示模式）

`quota.json` 的常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `percent` | 二选一 | 已用百分比（0–100，可略超 100） |
| `used` / `total` | 二选一 | 已用 / 总量；没有 `percent` 时按 `used / total` 计算 |
| `unit` | 否 | 单位，如 `次`、`点` |
| `product` | 否 | `coding-plan` / `agent-plan` / 自定义 |
| `tier` | 否 | `lite` / `pro` / `small` 等 |
| `source_label` | 否 | 气泡里显示的来源文字 |
| `reset_at` | 否 | 下次重置时间，ISO 格式；用于倒计时和重置检测 |
| `demo` | 否 | `true` 时标为「演示」，且**不触发低额度提醒** |

快速改数值 / 模拟低额度：

```powershell
# 37.4% 已用（保留原 demo 标记）
.venv\Scripts\python.exe scripts\set_quota.py 37.4

# 模拟真实低额度：剩余 3.5%，会变红、弹托盘提醒，并主动弹出红卡气泡
.venv\Scripts\python.exe scripts\set_quota.py 96.5 --no-demo

# 按用量设置，3 天后重置
.venv\Scripts\python.exe scripts\set_quota.py --used 19300 --total 20000 --unit 次 --reset-days 3
```

修改后等下一次轮询（默认 60 秒），或把鼠标移到宠物上立即刷新。

### 没有额度时怎么录（录制模式）

额度数据可以完全走本地文件，**提醒链路还是真的**：低额度会变红、发托盘通知、气泡主动弹出；
重置会被检测到并弹出满格卡。适合没买套餐、但要先出片的情况。

```powershell
# 1) 启动桌宠：只读本地 quota.json，轮询调快到 5 秒
set DBP_QUOTA_SOURCE=file
set DBP_POLL_SEC=5
.venv\Scripts\python.exe main.py

# 2) 另开一个终端，按回车走剧本：72% → 9.5%（低额度）→ 重置
.venv\Scripts\python.exe scripts\demo_sequence.py

# 3) 拍完恢复原来的 quota.json
.venv\Scripts\python.exe scripts\demo_sequence.py --restore
```

`demo_sequence.py` 第一次运行会把原文件备份成 `quota.json.rec.bak`。
写进去的数据是 `demo: false`、来源写「Coding Plan」，**卡片上不会出现「演示数据」字样**。

## 配置速查（`config.py`）

外观类数值（`PET_SCALE` / `FLOAT_*` / `BREATH_SCALE` / `ANIM_FPS` / `BUBBLE_*OFFSET*` / `BUBBLE_AUTOHIDE_SEC` / `START_DX` / `START_DY` / `LOW_QUOTA_REMAINING_PERCENT`）可以先在「配置预览」里调好并写回，其余配置直接改本文件。

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| `PET_IMAGE` | `assets/pet.png` | 宠物图片 |
| `PET_SCALE` | `0.19` | 缩放（原图 1279×1605）；气泡文字与排版会同步缩放 |
| `PET_MODE` | `"float"` | 启动状态：`float` 悬浮 / `gravity` 重力 |
| `FLOAT_AMPLITUDE_PX` | `6` | 上下漂浮幅度 |
| `FLOAT_PERIOD_SEC` | `2.4` | 漂浮周期 |
| `BREATH_SCALE` | `0.03` | 呼吸缩放幅度 |
| `ANIM_FPS` | `30` | 悬浮动画帧率 |
| `BUBBLE_AUTOHIDE_SEC` | `6` | 气泡自动收起时间 |
| `KEYCAST_ENABLED` | `False` | 启动时开启全局按键显示 |
| `KEYCAST_AUTOHIDE_SEC` | `1.2` | 停手后按键气泡保持时间 |
| `CLICK_EGG_REPLIES` | 多条文案 | 点击宠物随机显示的彩蛋（支持 `\n` 换行） |
| `DOUBAO_CHAT_URL` | `https://www.doubao.com/chat/` | 右键「打开豆包」跳转的地址 |
| `BUBBLE_OFFSET_X_RATIO` | `-0.5` | 气泡水平偏移（相对宠物宽度） |
| `BUBBLE_MIRROR_OFFSET_X_RATIO` | `0.5` | 镜像状态下的气泡偏移 |
| `BUBBLE_DEFAULT_FLIP_X` | `True` | 气泡默认水平翻转（尾迹朝右） |
| `START_ANCHOR` | `"bottom-left"` | 启动位置 |
| `START_DX` / `START_DY` | `80` / `60` | 距屏幕边缘的内边距 |
| `QUOTA_SOURCE` | `"auto"` | 额度数据源 |
| `QUOTA_PRODUCT` | `"coding-plan"` | 套餐偏好 |
| `QUOTA_SHOW_REMAINING` | `True` | `True` 显示剩余百分比；`False` 显示已用 |
| `LOW_QUOTA_WARN_ENABLED` | `True` | 低额度提醒开关 |
| `LOW_QUOTA_REMAINING_PERCENT` | `10.0` | 剩余低于此值时警示 |
| `POLL_INTERVAL_SEC` | `60` | 自动刷新间隔（最低 30 秒） |
| `HTTP_TIMEOUT` | `(3, 10)` | 连接 / 读取超时 |
| `ARKCLI_TIMEOUT_SEC` | `30` | arkcli 额度查询超时 |
| `ARKCLI_LOGIN_VISIBLE` | `False` | `False` 后台登录；`True` 显示登录窗口 |
| `ARKCLI_LOGIN_TIMEOUT_SEC` | `180` | 后台登录最长等待时间 |
| `VOLC_REGION` | `"cn-beijing"` | 火山 OpenAPI 区域 |
| `QUOTA_FILE` | `"quota.json"` | 本地额度文件 |
| `LOG_LEVEL` | `"INFO"` | 日志级别 |

修改 `config.py` 后重启桌宠生效（额度文件 `quota.json` 不需要重启）。

## 额度气泡说明

- 标题是「套餐 · 窗口」+ 剩余 / 已用额度，例如 `Agent Plan · 5 小时窗口剩余额度`、`Coding Plan · 会话窗口剩余额度`；主数字默认显示**剩余百分比**，进度条也按剩余量绘制；`QUOTA_SHOW_REMAINING = False` 可改成显示已用。
- 下方显示 `已用 6,200 / 10,000 点`（Agent Plan 直连 / 有绝对用量时）、数据来源 / 演示标记、重置倒计时和更新时间。
- 两次刷新之间主窗口额度增加时，显示「5 小时窗口额度消耗 +x%」；跨周期下降时显示「新周期的…额度已重置」；主窗口切换（例如 5h 重置后不再是最高）时不弹变化，避免误报。
- 低额度按**主窗口**剩余百分比判断——先撞的通常就是 5 小时 / 会话窗口。
- 剩余 ≤ 30%：数字和进度条变橙；剩余 ≤ 10%（或 `LOW_QUOTA_REMAINING_PERCENT`）：变红、宠物右上角出现 `!` 角标，并在跨越阈值时发一次系统通知。
- 气泡默认水平翻转（尾迹朝右）；宠物到右半边镜像时气泡会再翻转一次（尾迹朝左）；`BUBBLE_DEFAULT_FLIP_X = False` 则与宠物镜像方向一致。
- 宠物中心进入当前显示器右半边时水平镜像，回到左半边后自动恢复。
- 点击宠物的彩蛋文案来自 `CLICK_EGG_REPLIES`，每次随机一条；可增删条目，留空会回退为「豆包豆包」。文案中写 `\n` 可以换行：单行文案套进卡片模板（与演示片里「好的。」那张卡片一致），多行文案会把整颗气泡让给文字、最后一行放大作为重点，长前情自动缩小。没有额度数据（登录失效 / 未开通）时照样套卡片，但标题换成「额度状态」、不摆「剩余额度」标签，避免假装在报数。
- `demo: true` 的演示数据只展示、不触发低额度警示。

## 日志与排查

日志：`%LOCALAPPDATA%\DoubaoPet\pet.log`（256 KB 轮转，保留 2 份）。

常见状态 / 错误：

| 日志 / 气泡 | 处理 |
| --- | --- |
| `未找到额度来源` | 安装 arkcli、配置 AK/SK，或创建 `quota.json` |
| `arkcli 未登录或登录已过期` | 右键「连接额度 / 登录」，或运行 `arkcli auth login volc-sso`；无浏览器环境用 `--no-browser` 两段式登录 |
| SSO 剩余时间不足 | `arkcli auth status` 会显示 `id_token_remaining`；过期后重新登录即可，已保存的 profile 不会丢 |
| `未开通` / `没有已开通的 Agent Plan / Coding Plan` | 去火山方舟控制台订阅；点「立即刷新」重试（`arkcli plans get` 为空时会这样提示） |
| `未找到已订阅的 Coding Plan / Agent Plan` | 确认账号已订阅；或把 `QUOTA_PRODUCT` 改成另一种套餐 |
| `火山 AK/SK 鉴权失败` | 检查 AK/SK、区域和账号权限 |
| `HTTP 401 / 403` | 检查 `QUOTA_HTTP_TOKEN` / `QUOTA_HTTP_HEADERS` |
| `quota.json 解析失败` | 检查 JSON 格式和字段名 |
| 气泡不出现 | 鼠标确实移到宠物贴图上；看日志是否有「气泡弹出」；演示数据约 6 秒后会自动收起 |
| 桌宠不见了 | 双击启动器唤起；或从托盘菜单「显示宠物」 |

认证 / 配置 / 未开通类错误会停止自动重试，避免反复弹错；修好后点击宠物或「立即刷新」即可重试。**网络类临时错误不会停止重试**，会按 3 → 6 → … → 300 秒退避继续尝试（以前 arkcli 的一次 `GetAFPUsage` 超时会被误判成"未订阅"而永久停更）。

## 测试与开发

先退出正在运行的桌宠，再执行：

```powershell
# 46 项额度解析 / 多窗口 / 变化检测 / 低额度 / 直连限制测试（不联网）
.venv\Scripts\python.exe scripts\test_quota_logic.py

# 13 项按键标签 / 组合键顺序测试（不安装键盘钩子）
.venv\Scripts\python.exe scripts\test_keycast_logic.py

# 预览并截图气泡（Agent Plan 5 小时窗口 / Coding Plan 会话窗口低额度 / 扁平演示）
.venv\Scripts\python.exe scripts\preview_bubble.py --plan agent --used 62
.venv\Scripts\python.exe scripts\preview_bubble.py --plan coding --used 96.5 --low
.venv\Scripts\python.exe scripts\preview_bubble.py --used 37.4 --demo

# 单独打开配置预览窗口（数值填写 + 模拟效果）
.venv\Scripts\python.exe config_preview.py

# 修改演示额度
.venv\Scripts\python.exe scripts\set_quota.py 96.5 --no-demo
```

预览截图默认保存到 `%TEMP%\opencode\doubao_preview.png`。

## 文件结构

| 文件 / 目录 | 作用 |
| --- | --- |
| `main.py` | 程序入口、单实例、托盘 / 菜单 / 信号接线 |
| `ui.py` | 宠物窗口、气泡、进度条、动画、键帽绘制、托盘 |
| `keycast.py` | Windows 全局键盘钩子、组合键标签整理 |
| `config_preview.py` | 配置预览对话框：数值填写、模拟舞台、写回 `config.py` |
| `quota.py` | 额度轮询、多窗口解析（arkcli / `GetCodingPlanUsage` / `GetAFPUsage` / HTTP / 文件）、变化与低额度检测 |
| `config.py` | 用户配置（常改项都在这里） |
| `quota.json` | 本地演示额度数据 |
| `assets/pet.png` | 豆包贴图 |
| `assets/bubble.png` / `bubble.svg` | 旧版手绘气泡素材，**已不再使用**（气泡改为代码绘制，保留备查） |
| `scripts/` | 额度设置、逻辑测试、气泡预览（含截图） |
| `启动桌宠.vbs` | 无控制台启动器 |
| `requirements.txt` | 依赖清单 |

## 环境

项目内已带 `.venv`（Python 3.15、PySide6 6.12、requests 2.34）。需要重建时：

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 已知边界

- 外观类配置可以在「配置预览」里用输入框调整并写回 `config.py`；额度数据源、登录方式等其余项仍然直接改 `config.py`。没有开机自启、位置记忆。
- 全局按键显示依赖 Windows 低级键盘钩子，UAC 安全桌面和管理员权限窗口中的按键通常捕获不到；它只用于气泡实时显示，不记录按键内容。
- AK/SK 直连覆盖个人版 Coding Plan（`GetCodingPlanUsage`）与 Agent Plan（`GetAFPUsage`）；两种团队版需要 seat id，走 arkcli，不在此直连路径内。
- 一次只显示一个主套餐：账号同时有 Coding Plan 与 Agent Plan 时，按 `QUOTA_PRODUCT` 选一个（回退时标题会显示实际套餐），不会并排显示两个。
- arkcli 的 `periods` 只有 `label` + `percent`，没有绝对用量和重置时间；想要「已用 8,750 / 35,000 点」和重置倒计时，需要用 AK/SK 直连 `GetAFPUsage`（Agent Plan）或等 arkcli 后续版本。
- 真实 arkcli / AK/SK 数据需要本机登录或配置凭据后才能显示；未安装 arkcli 且未配置 AK/SK 时，会回退到 `quota.json` 演示数据，并在气泡里明确标注「演示」。若已安装 arkcli 但未登录，`auto` 会选中 arkcli 并在气泡提示登录，而不是静默切回演示数据。
- `arkcli` / 火山 OpenAPI 的字段若随版本变化，气泡会显示解析错误；日志里有原始错误类别，便于定位。
