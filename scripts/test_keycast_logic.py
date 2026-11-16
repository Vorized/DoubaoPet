# -*- coding: utf-8 -*-
"""全局按键显示的键名 / 组合键整理单元测试。

运行（在项目根目录）：
    .venv\\Scripts\\python.exe scripts\\test_keycast_logic.py

只测试纯逻辑，不安装全局键盘钩子、不产生真实按键。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import keycast  # noqa: E402


class KeyLabelTests(unittest.TestCase):
    def test_letters_and_digits(self) -> None:
        self.assertEqual(keycast.key_label(0x41), "A")
        self.assertEqual(keycast.key_label(0x5A), "Z")
        self.assertEqual(keycast.key_label(0x30), "0")
        self.assertEqual(keycast.key_label(0x39), "9")

    def test_function_keys(self) -> None:
        self.assertEqual(keycast.key_label(0x70), "F1")
        self.assertEqual(keycast.key_label(0x7B), "F12")
        self.assertEqual(keycast.key_label(0x87), "F24")

    def test_numpad_keys(self) -> None:
        self.assertEqual(keycast.key_label(0x60), "Num0")
        self.assertEqual(keycast.key_label(0x69), "Num9")
        self.assertEqual(keycast.key_label(0x6B), "Num+")
        self.assertEqual(keycast.key_label(0x6F), "Num/")

    def test_special_keys(self) -> None:
        self.assertEqual(keycast.key_label(0x08), "Bksp")
        self.assertEqual(keycast.key_label(0x09), "Tab")
        self.assertEqual(keycast.key_label(0x0D), "Enter")
        self.assertEqual(keycast.key_label(0x1B), "Esc")
        self.assertEqual(keycast.key_label(0x20), "Space")
        self.assertEqual(keycast.key_label(0x2D), "Ins")
        self.assertEqual(keycast.key_label(0x2E), "Del")

    def test_arrow_keys(self) -> None:
        self.assertEqual(keycast.key_label(0x25), "←")
        self.assertEqual(keycast.key_label(0x26), "↑")
        self.assertEqual(keycast.key_label(0x27), "→")
        self.assertEqual(keycast.key_label(0x28), "↓")

    def test_oem_punctuation(self) -> None:
        self.assertEqual(keycast.key_label(0xBA), ";")
        self.assertEqual(keycast.key_label(0xBB), "=")
        self.assertEqual(keycast.key_label(0xBC), ",")
        self.assertEqual(keycast.key_label(0xDB), "[")
        self.assertEqual(keycast.key_label(0xDC), "\\")


class ComboTests(unittest.TestCase):
    def test_single_key(self) -> None:
        self.assertEqual(keycast.tokens_for(0x41), ["A"])

    def test_ctrl_combo(self) -> None:
        self.assertEqual(keycast.tokens_for(0x43, {"ctrl"}), ["Ctrl", "C"])

    def test_modifier_order(self) -> None:
        self.assertEqual(
            keycast.tokens_for(0x41, {"win", "alt", "shift", "ctrl"}),
            ["Ctrl", "Shift", "Alt", "Win", "A"],
        )

    def test_modifier_only(self) -> None:
        self.assertEqual(keycast.tokens_for(0xA2), ["Ctrl"])
        self.assertEqual(keycast.tokens_for(0xA0), ["Shift"])
        self.assertEqual(keycast.tokens_for(0x5B), ["Win"])

    def test_modifier_added_when_async_state_lags(self) -> None:
        # 按 Shift 的瞬间 GetAsyncKeyState 可能还没反映出来，仍应显示 Shift。
        self.assertEqual(keycast.tokens_for(0xA0, {"ctrl"}), ["Ctrl", "Shift"])

    def test_ime_processing_keys_ignored(self) -> None:
        self.assertEqual(keycast.tokens_for(keycast.VK_PROCESSKEY, {"ctrl"}), [])
        self.assertEqual(keycast.tokens_for(keycast.VK_PACKET), [])

    def test_oem_combo(self) -> None:
        self.assertEqual(keycast.tokens_for(0xBB, {"ctrl", "shift"}),
                         ["Ctrl", "Shift", "="])


if __name__ == "__main__":
    unittest.main(verbosity=2)
