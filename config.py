# -*- coding: utf-8 -*-
"""
豆包桌宠 —— ★ 用户配置 ★
==========================
所有可能需要调整的内容都集中在本文件；改完保存、重启桌宠即可生效。
其他 .py 文件一般情况下不用动。
"""

import os
from pathlib import Path

# 程序所在目录（自动识别，无需修改）
BASE_DIR = Path(__file__).resolve().parent

# ========================= 宠物外观 =========================

# 宠物图片：相对本目录或绝对路径
PET_IMAGE = "assets/pet.png"

# 气泡图片
BUBBLE_IMAGE = "assets/bubble.png"

# 显示缩放（1.0 = 原图 1279px 宽；0.28 ≈ 358px 宽，适合桌面常驻）
# 气泡会随宠物尺寸等比缩放，字号 / 行高 / 进度条 / 尾迹间距会同步调整
PET_SCALE = 0.19

# 上下浮动：幅度（像素）；FLOAT_PERIOD_SEC 是一个完整来回的周期（秒）
FLOAT_AMPLITUDE_PX = 6
FLOAT_PERIOD_SEC = 2.4

# 呼吸缩放幅度（0.03 = ±3%）
BREATH_SCALE = 0.03

# 动画帧率（30 已足够顺滑；调低更省 CPU）
ANIM_FPS = 30

# 宠物初始状态：
#   "float"   = 悬浮状态（呼吸 + 漂浮动画，默认）
#   "gravity" = 重力状态（静态画面、无呼吸/漂浮；以任务栏为地面，拖到高处松手会自动落回地面）
# 运行中可右键宠物（或用托盘菜单）勾选「重力模式」随时切换，重启后以本配置为准
PET_MODE = "float"

# 额度气泡自动收起时间（秒）；鼠标停在气泡上时不会收起
BUBBLE_AUTOHIDE_SEC = 6

# 点击宠物彩蛋：可配置多条文案，每次点击随机显示一条
# 留空时回退为 ["豆包豆包"]；需要换行时直接写 \n
# 两行文案会按“小字前情 + 大字重点”排版
CLICK_EGG_REPLIES = [
    "？！豆豆！？",
    "？！包包！？",
    "你好呀 我是豆包",
    "我用最直白最不绕弯子的方式告诉你：\n我不知道",
]

# 额度气泡水平偏移（相对宠物宽度；正值向右、负值向左）
# 0 = 气泡尾迹正对宠物头顶中心
BUBBLE_OFFSET_X_RATIO = -0.5

# 水平镜像状态（宠物在显示器右半边、贴图翻转）下的气泡水平偏移，相对宠物宽度
# 镜像时“替换”上面的 BUBBLE_OFFSET_X_RATIO（不是叠加）
BUBBLE_MIRROR_OFFSET_X_RATIO = 0.5

# 气泡默认水平翻转：True = 尾迹朝右、主体在宠物左侧（宠物在左半边时）
# 宠物到右半边镜像时，气泡会在此基础上再翻转一次（尾迹朝左）
BUBBLE_DEFAULT_FLIP_X = True

# 是否始终置顶
ALWAYS_ON_TOP = True

# ========================= 启动位置 =========================

# 锚点："bottom-right" / "bottom-left" / "top-right" / "top-left" / "center"
# START_DX / START_DY：相对锚点边缘的内边距（像素）
START_ANCHOR = "bottom-left"
START_DX = 80
START_DY = 60

# ==================== 套餐额度数据源 ====================

# 数据源（一般保持 "auto" 即可）：
#   "auto"   = 自动选择：arkcli（已安装）→ AK/SK 直连 → quota.json
#   "arkcli" = 调用 `arkcli usage plan --all --format json`（推荐；走 SSO 登录，无需 AK/SK）
#   "api"    = 用下面的 VOLC_ACCESS_KEY / VOLC_SECRET_KEY 直连火山 OpenAPI
#              （Coding Plan → GetCodingPlanUsage；Agent Plan → GetAFPUsage）
#   "http"   = 从自定义额度接口拉取 JSON（适合自建服务 / 中转站）
#   "file"   = 只读取本地 quota.json（可手动编辑，或由其他脚本写入）
QUOTA_SOURCE = os.environ.get("DBP_QUOTA_SOURCE", "auto")
# 录制时可临时用环境变量指到 file，避免没额度时一直报错：
#   set DBP_QUOTA_SOURCE=file

# 套餐类型："coding-plan" / "agent-plan"
# 两个套餐都有自己的多窗口（Coding Plan：会话/周/月；Agent Plan：5 小时/周/月）。
# 气泡主数字取「最紧窗口」＝已用百分比最高的那个，标题里会标出窗口名；
# 低额度提醒、重置检测都基于这个主窗口（真正先卡住你的是 5 小时/会话窗口）。
# arkcli 优先返回 QUOTA_PRODUCT 指定的套餐，找不到时回退另一种（标题会显示实际套餐）；
# 团队版（*-team）需要 seat id，目前只支持 arkcli。
QUOTA_PRODUCT = "coding-plan"

# 气泡主数字显示“剩余”还是“已用”：
#   True  = 显示剩余百分比（默认，进度条也是剩余量，额度不足时更直观）
#   False = 显示已用百分比
QUOTA_SHOW_REMAINING = True

# 低额度提醒：剩余百分比低于该值时变红 + 托盘通知（只在跨越阈值时提醒一次）
LOW_QUOTA_WARN_ENABLED = True
LOW_QUOTA_REMAINING_PERCENT = 10.0

# 额度自动刷新间隔（秒），请勿低于 30（录制时可用环境变量临时调快：DBP_POLL_SEC=5）
POLL_INTERVAL_SEC = max(2.0, float(os.environ.get("DBP_POLL_SEC", 60)))

# 网络超时（连接秒, 读取秒）
HTTP_TIMEOUT = (3, 10)

# ---- arkcli（推荐） ----
# 需要 Node.js；安装：npm i -g @volcengine/ark-cli
# 登录：arkcli auth login volc-sso
# 检查：arkcli auth status / arkcli usage plan --all --format json
# --all 会同时查询个人版/团队版的 Coding Plan 与 Agent Plan；
# 只订阅了 Agent Plan 时，不带 --all 的自动发现可能返回空列表。
# 返回的 periods 含 5h / weekly / monthly 三个窗口，桌宠会全部读取。
ARKCLI_COMMAND = ("arkcli", "usage", "plan", "--all", "--format", "json")
ARKCLI_TIMEOUT_SEC = 30

# 「连接额度 / 登录」的登录方式：
# False = 后台登录（推荐）：只打开浏览器，不弹出 PowerShell / 命令行窗口
# True  = 显示可见登录窗口：首次登录需要手动选择项目，或浏览器未自动打开时使用
ARKCLI_LOGIN_VISIBLE = False

# 后台登录最长等待时间（秒）；超时后桌宠会提示重试或改用可见窗口
ARKCLI_LOGIN_TIMEOUT_SEC = 180

# ---- 火山 OpenAPI 直连（可选；需要 AK/SK，不是方舟推理 API Key） ----
# 个人版两个套餐都支持：Coding Plan → GetCodingPlanUsage（只有百分比）；
# Agent Plan → GetAFPUsage（带 Quota / Used / ResetTime）。团队版请用 arkcli。
# 建议用环境变量 VOLC_ACCESS_KEY / VOLC_SECRET_KEY，避免明文写在文件里
VOLC_ACCESS_KEY = ""
VOLC_SECRET_KEY = ""
VOLC_REGION = "cn-beijing"

# ---- 自定义 HTTP 接口（可选） ----
# 期望返回 JSON，例如：
#   {"percent": 37.4, "used": 7480, "total": 20000, "unit": "次",
#    "reset_at": "2026-11-01T00:00:00+08:00"}
# 也兼容 arkcli usage plan / GetCodingPlanUsage 的完整返回结构
QUOTA_HTTP_URL = ""
QUOTA_HTTP_TOKEN = ""
QUOTA_HTTP_HEADERS = {}

# ---- 本地额度文件 ----
# 手动模式 / 其他脚本写入；字段说明见 README
QUOTA_FILE = "quota.json"

# ==================== 全局按键显示 ====================

# 开启后全局监听键盘，用气泡显示最近一次组合键（如 Ctrl + Shift + S）。
# 默认关闭，保护隐私；也可在右键菜单「按键显示（全局）」随时开关，
# 菜单开关只对本次运行有效，重启后仍以本配置为准。
# 不记录按键内容、不写日志；UAC 安全桌面和管理员权限窗口中的按键通常无法捕获。
KEYCAST_ENABLED = False

# 停止按键后，按键气泡保持多久再自动收起（秒）
KEYCAST_AUTOHIDE_SEC = 1.2

# ========================= 其他 =========================

LOG_LEVEL = "INFO"  # DEBUG / INFO / WARNING
APP_NAME = "豆包桌宠"

# 右键菜单「打开豆包」跳转的地址
DOUBAO_CHAT_URL = "https://www.doubao.com/chat/"
