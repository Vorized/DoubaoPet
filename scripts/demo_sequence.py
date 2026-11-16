# -*- coding: utf-8 -*-
"""录制用的额度剧本：一步一步改 quota.json，按回车推进。

没有买额度也能拍「额度」这条线——数据来自本地文件，功能和提醒都是真的，
只是数字是写进去的。

用法（在项目根目录）：

    # 1) 先这样启动桌宠：额度只读本地文件，轮询 5 秒一次
    set DBP_QUOTA_SOURCE=file
    set DBP_POLL_SEC=5
    .venv\\Scripts\\python.exe main.py

    # 2) 另开一个终端，跑这个剧本
    .venv\\Scripts\\python.exe scripts\\demo_sequence.py

    # 3) 拍完恢复原来的 quota.json
    .venv\\Scripts\\python.exe scripts\\demo_sequence.py --restore

每一步写完后，桌宠会在下一次轮询时读到新值（DBP_POLL_SEC=5 就是 5 秒内）。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUOTA = ROOT / "quota.json"
BACKUP = ROOT / "quota.json.rec.bak"

# (标题, 传给 set_quota.py 的参数, 这一步该拍什么)
STEPS = [
    (
        "额度充足 · 72%",
        ["28", "--no-demo", "--label", "Coding Plan", "--reset-days", "3"],
        "悬停看额度、按键显示、右键菜单——都用这一档。",
    ),
    (
        "跌破阈值 · 剩余 9.5%",
        ["90.5", "--no-demo", "--label", "Coding Plan", "--reset-days", "3"],
        "等一次轮询：角标变红 + 托盘通知 + 气泡自己弹出来（这次不用你悬停）。",
    ),
    (
        "额度重置 · 100%",
        ["0", "--no-demo", "--label", "Coding Plan", "--reset-days", "5"],
        "再等一次轮询：托盘「额度已重置」，气泡自己弹满格。",
    ),
]


def run_set_quota(args: list[str], path: str | None) -> None:
    cmd = [sys.executable, str(ROOT / "scripts" / "set_quota.py"), *args]
    if path:
        cmd += ["--path", path]
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit(f"set_quota.py 失败（退出码 {result.returncode}）")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="录制用的额度剧本")
    parser.add_argument("--restore", action="store_true", help="把 quota.json 恢复成跑剧本之前的样子")
    parser.add_argument("--path", help="quota.json 路径（默认项目根目录）")
    args = parser.parse_args()

    target = Path(args.path) if args.path else QUOTA

    if args.restore:
        if not BACKUP.exists():
            print("没有找到备份 quota.json.rec.bak，无需恢复。")
            return 0
        shutil.copyfile(BACKUP, target if args.path else QUOTA)
        BACKUP.unlink()
        print("已恢复 quota.json。")
        return 0

    if target == QUOTA and not BACKUP.exists() and QUOTA.exists():
        shutil.copyfile(QUOTA, BACKUP)
        print(f"已备份原 quota.json → {BACKUP.name}")

    print("\n额度剧本共 %d 步。桌宠要用 DBP_QUOTA_SOURCE=file 启动，轮询间隔越小越好。" % len(STEPS))
    for i, (title, cmd_args, cue) in enumerate(STEPS, 1):
        print(f"\n──────── {i}/{len(STEPS)} · {title} ────────")
        print(f"  这一步拍：{cue}")
        input("  准备好后按回车写入…")
        run_set_quota(cmd_args, args.path)
        print("  已写入。等下一次轮询生效（DBP_POLL_SEC 秒内）。")
        input("  拍完按回车进入下一步…")

    print("\n三步都写完了。恢复原文件：")
    print("  .venv\\Scripts\\python.exe scripts\\demo_sequence.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
