#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_screen.py — v0.3 屏幕/GUI 的 Linux 自测（不用真屏幕、不用 Windows）。

测的是纯逻辑 + 安全门：
  1. clamp_point：坐标夹到屏幕内，越界的截断不报错
  2. downscale_size：大图等比缩到最长边 1280
  3. parse_key：Enter / Esc / Ctrl+S / F5 / 方向键 / 乱写的按键名要拒绝
  4. log_preview：打字日志只记前 20 字 + 长度
  5. type_text 长度上限：超过 2000 字拒绝（不用真键盘，用 mock 的 win32）
  6. GUI 开关：没有 gui_enabled.txt 时 click/screenshot/get_window 一律拒绝，
     建了文件后才放行到 screen.py（Linux 上 screen.py 会报“仅限 Windows”，
     这一步只证明开关本身在工作，不证明真点了鼠标）
  7. 回归：v0.2 的 list_dir / run_python 任务类型还在 HANDLERS 里

跑：python3 test_screen.py   （Linux / Windows 都能跑）
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import screen  # noqa: E402
import windows_agent  # noqa: E402

passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS %s" % name)
    else:
        failed += 1
        print("FAIL %s %s" % (name, extra))


# 1. 坐标夹取
check("clamp inside", screen.clamp_point(100, 200, 1920, 1080) == (100, 200, False))
check("clamp x overflow", screen.clamp_point(9999, 200, 1920, 1080) == (1919, 200, True))
check("clamp negative", screen.clamp_point(-5, -10, 1920, 1080) == (0, 0, True))
check("clamp edge", screen.clamp_point(1919, 1079, 1920, 1080) == (1919, 1079, False))
try:
    screen.clamp_point("a", 1, 1920, 1080)
    check("clamp bad type rejected", False)
except ValueError:
    check("clamp bad type rejected", True)

# 2. 缩放
check("downscale small unchanged", screen.downscale_size(800, 600) == (800, 600))
check("downscale 4k", screen.downscale_size(3840, 2160) == (1280, 720))
check("downscale portrait", screen.downscale_size(1000, 3000) == (426, 1280))

# 3. 按键解析
check("parse Enter", screen.parse_key("Enter") == (0x0D, set()))
check("parse Esc", screen.parse_key("esc")[0] == 0x1B)
check("parse Ctrl+S", screen.parse_key("Ctrl+S") == (ord("S"), {"ctrl"}))
check("parse F5", screen.parse_key("F5")[0] == 0x74)
check("parse Alt+Tab", screen.parse_key("Alt+Tab") == (0x09, {"alt"}))
check("parse Up", screen.parse_key("Up")[0] == 0x26)
try:
    screen.parse_key("Ctrl+Alt+Delete+Foo")
    check("parse garbage rejected", False)
except ValueError:
    check("parse garbage rejected", True)
try:
    screen.parse_key("")
    check("parse empty rejected", False)
except ValueError:
    check("parse empty rejected", True)

# 4. 打字日志预览：不记全量
prev = screen.log_preview("a" * 100)
check("log_preview truncates", len(prev) < 100 and "100 chars" in prev, prev)
check("log_preview short", screen.log_preview("hi") == "hi(2 chars)")

# 5. type_text 长度上限（mock 掉 win32 调用，只测长度门）
_calls = []


def _fake_type_ascii(text):
    _calls.append(("ascii", text))


def _fake_clipboard(text):
    _calls.append(("clipboard", text))


screen._type_ascii = _fake_type_ascii
screen._type_via_clipboard = _fake_clipboard
r = screen.type_text("hello")
check("type ascii ok", r.get("ok") and r.get("method") == "keyboard", str(r))
r = screen.type_text("你好 SolidWorks")
check("type non-ascii via clipboard", r.get("ok") and r.get("method") == "clipboard", str(r))
r = screen.type_text("x" * 2001)
check("type too long rejected", not r.get("ok"), str(r))
r = screen.type_text("")
check("type empty rejected", not r.get("ok"))

# 6. GUI 开关：缺文件拒绝，有文件才放行到 screen（Linux 上报仅限 Windows）
tmp = tempfile.mkdtemp()
windows_agent.GUI_ENABLED_FILE = os.path.join(tmp, "gui_enabled.txt")
cfg = {"allowed_folders": [tmp]}
check("gui off: click refused",
      "未启用" in str(windows_agent.do_click({"x": 10, "y": 10}, cfg).get("error", "")))
check("gui off: screenshot refused",
      "未启用" in str(windows_agent.do_screenshot({}, cfg).get("error", "")))
check("gui off: get_window refused",
      "未启用" in str(windows_agent.do_get_window({}, cfg).get("error", "")))
open(windows_agent.GUI_ENABLED_FILE, "w").write("")
# 有开关后，Linux 上 screen 会报缺少 pywin32 / 仅限 Windows——证明门开了，不是被开关拦住
r = windows_agent.do_click({"x": 10, "y": 10}, cfg)
check("gui on: click reaches screen layer",
      "未启用" not in str(r.get("error", "")), str(r))
r = windows_agent.do_click({"x": 10}, cfg)
check("click missing y rejected", not r.get("ok") and "y" in str(r.get("error", "")))
r = windows_agent.do_press_key({"key": "NoSuchKey123"}, cfg)
check("press bad key rejected", not r.get("ok"), str(r))

# 7. 回归：v0.2 任务类型还在
for t in ("list_dir", "read_file", "write_file", "run_python",
          "run_zwcad_script", "screenshot"):
    check("handler still present: " + t, t in windows_agent.HANDLERS)
for t in ("click", "double_click", "type_text", "press_key",
          "get_window", "read_file_base64"):
    check("handler added: " + t, t in windows_agent.HANDLERS)

print("\n%d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
