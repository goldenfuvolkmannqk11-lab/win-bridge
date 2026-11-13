#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge v0.3 screen.py — Windows 屏幕监控 + 图形界面控制
（只在 Matsuda 的 Windows 电脑上跑，Linux 上 import 没问题，但调用会报“仅限 Windows”）

只用标准库 + pywin32 + Pillow，不装别的。
  - screenshot(): 截全屏（或按标题找一个窗口），太大就缩到最长边 1280，
    存 JPG（quality 75）到工作文件夹，返回路径 / 宽高 / 大小。
  - click(x, y) / double_click(x, y): 鼠标点一下 / 点两下。
  - press_key(name): 按一个键，名字见 KEY_MAP（Enter / Esc / Tab / F1-F12 /
    Ctrl+C / Ctrl+V / Ctrl+S / Alt+Tab 等）。
  - type_text(text): 打字。纯 ASCII 走键盘事件；中文 / 其它非 ASCII
    走剪贴板粘贴（Ctrl+V），会改剪贴板内容，用完不恢复——README 里写明了。
  - get_foreground_window(): 当前前台窗口的标题 + 位置 + 大小。
  - get_screen_size(): 主显示器宽高。

安全：
  - 坐标一定夹到屏幕内，点不到屏幕外面。
  - 所有函数都不自己决定“该点哪里”，只执行任务里明确给的坐标 / 按键 / 文字。
  - 绝不要用 type_text 输密码：审计日志会记下打字内容的前 20 个字符
    （只记长度 + 前 20 字，用来查错），密码请 Matsuda 自己手输。
"""

import os
import time

MAX_SCREENSHOT_WIDTH = 1280   # 截屏最长边，超过就等比缩小
SCREENSHOT_QUALITY = 75       # JPG 质量，控制在几百 KB 以内，好回传
MAX_TYPE_CHARS = 2000         # 一次打字最多 2000 字，防误操作刷屏


# ---------------- 纯函数：Linux 上也能测，不用真屏幕 ----------------

def clamp_point(x, y, width, height):
    """把坐标夹到屏幕内。返回 (x, y, clamped?)。"""
    try:
        x = int(x)
        y = int(y)
    except Exception:
        raise ValueError("坐标必须是数字: x=%r y=%r" % (x, y))
    cx = max(0, min(x, int(width) - 1))
    cy = max(0, min(y, int(height) - 1))
    return cx, cy, (cx != x or cy != y)


def downscale_size(width, height, max_side=MAX_SCREENSHOT_WIDTH):
    """等比缩小到最长边 <= max_side。返回 (w, h)。"""
    if width <= max_side and height <= max_side:
        return int(width), int(height)
    scale = max_side / float(max(width, height))
    return max(1, int(width * scale)), max(1, int(height * scale))


def log_preview(text, limit=20):
    """审计日志用的打字预览：只记前 limit 个字符 + 总长度，绝不记全量。"""
    text = "" if text is None else str(text)
    return "%s...(%d chars)" % (text[:limit], len(text)) if len(text) > limit \
        else "%s(%d chars)" % (text, len(text))


def parse_key(name):
    """把按键名字转成 (vk_code, modifiers)。modifiers 是 'ctrl'/'alt'/'shift' 的子集。

    名字不区分大小写，接受 Enter / Esc / Escape / Tab / Space / Backspace /
    Delete / Home / End / PageUp / PageDown / Up / Down / Left / Right /
    F1..F12 / Ctrl+C / Ctrl+V / Ctrl+S / Ctrl+A / Ctrl+Z / Alt+Tab / Shift+Tab。
    不认识的名字报 ValueError，绝不乱按。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("按键名不能为空")
    parts = [p.strip().lower() for p in name.split("+") if p.strip()]
    mods = set()
    main = None
    for p in parts:
        if p in ("ctrl", "control"):
            mods.add("ctrl")
        elif p in ("alt",):
            mods.add("alt")
        elif p in ("shift",):
            mods.add("shift")
        elif main is None:
            main = p
        else:
            raise ValueError("按键名看不懂: %r" % name)
    if main is None:
        raise ValueError("按键名里没有主键: %r" % name)
    vk_map = {
        "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
        "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "insert": 0x2D, "capslock": 0x14,
    }
    for i in range(1, 13):
        vk_map["f%d" % i] = 0x6F + i
    if main in vk_map:
        return vk_map[main], mods
    if len(main) == 1:
        return ord(main.upper()), mods
    raise ValueError("不认识的按键: %r（支持 Enter/Esc/Tab/方向键/F1-F12/单字母/ctrl+字母）" % name)


def is_ascii_text(text):
    try:
        str(text).encode("ascii")
        return True
    except Exception:
        return False


# ---------------- Windows 专用：下面都要 win32 / Pillow ----------------

def _win32():
    """懒加载 pywin32，Linux / 没装时报清楚的中文错。"""
    try:
        import win32api
        import win32con
        import win32gui
        return win32api, win32con, win32gui
    except ImportError:
        raise RuntimeError("缺少 pywin32，请先运行 install_windows.bat 安装依赖。")


def get_screen_size():
    win32api, win32con, _ = _win32()
    w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
    return int(w), int(h)


def get_foreground_window():
    _, _, win32gui = _win32()
    hwnd = win32gui.GetForegroundWindow()
    title = ""
    rect = None
    try:
        title = win32gui.GetWindowText(hwnd) or ""
    except Exception:
        pass
    try:
        rect = win32gui.GetWindowRect(hwnd)
    except Exception:
        rect = None
    if rect:
        x0, y0, x1, y1 = (int(v) for v in rect)
        return {"ok": True, "title": title, "hwnd": int(hwnd),
                "x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
    return {"ok": True, "title": title, "hwnd": int(hwnd),
            "x": 0, "y": 0, "width": 0, "height": 0}


def _find_window_rect(title_substring):
    """按标题子串找一个窗口，返回 (hwnd, rect)；找不到返回 (None, None)。"""
    _, _, win32gui = _win32()
    found = []

    def _enum(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd) or ""
                if title_substring.lower() in title.lower():
                    found.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(_enum, None)
    if not found:
        return None, None
    hwnd = found[0]
    try:
        rect = win32gui.GetWindowRect(hwnd)
    except Exception:
        return hwnd, None
    return hwnd, tuple(int(v) for v in rect)


def screenshot(folder, window=None):
    """截屏，存 JPG 到 folder，返回 dict。

    window 为空截全屏；给标题子串就只截那个窗口（找不到窗口就报错，不退回全屏，
    免得误截别的窗口）。图片太大先等比缩到最长边 1280 再存。
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return {"ok": False, "error": "缺少 Pillow，请先运行 install_windows.bat 安装依赖。"}
    bbox = None
    if window:
        _, rect = _find_window_rect(window)
        if rect is None:
            return {"ok": False, "error": "找不到标题包含 %r 的窗口" % window}
        bbox = rect
    try:
        img = ImageGrab.grab(bbox=bbox)
    except Exception as e:
        return {"ok": False, "error": "截屏失败: %s" % e}
    orig_w, orig_h = img.width, img.height
    w, h = downscale_size(orig_w, orig_h)
    if (w, h) != (orig_w, orig_h):
        img = img.resize((w, h))
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    # ImageGrab 不截鼠标光标，所以手动把光标位置画上去：
    # 取当前光标坐标，按缩放比例换算，画一个红色圆圈 + 十字，
    # 这样截图里就能看到鼠标在哪了。取不到坐标也不报错。
    cursor = None
    try:
        win32api, _, _ = _win32()
        cx, cy = win32api.GetCursorPos()
        if bbox:
            cx -= bbox[0]
            cy -= bbox[1]
        scale = w / orig_w if orig_w else 1
        cx, cy = int(cx * scale), int(cy * scale)
        cursor = {"x": cx, "y": cy}
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        r = 12
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 0, 0), width=3)
        d.line([cx - r - 4, cy, cx + r + 4, cy], fill=(255, 0, 0), width=2)
        d.line([cx, cy - r - 4, cx, cy + r + 4], fill=(255, 0, 0), width=2)
    except Exception:
        cursor = None
    name = "screenshot_%s.jpg" % time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, name)
    try:
        img.save(path, "JPEG", quality=SCREENSHOT_QUALITY)
    except Exception as e:
        return {"ok": False, "error": "保存截屏失败: %s" % e}
    return {"ok": True, "path": path, "file": name,
            "width": w, "height": h, "size_bytes": os.path.getsize(path),
            "window": window or "", "cursor": cursor}


def _mouse_event(down_flag, up_flag, x, y):
    win32api, win32con, _ = _win32()
    width, height = get_screen_size()
    cx, cy, clamped = clamp_point(x, y, width, height)
    win32api.SetCursorPos((cx, cy))
    time.sleep(0.05)
    win32api.mouse_event(down_flag, 0, 0, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(up_flag, 0, 0, 0, 0)
    return cx, cy, clamped


def click(x, y):
    _, win32con, _ = _win32()
    cx, cy, clamped = _mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,
                                   win32con.MOUSEEVENTF_LEFTUP, x, y)
    return {"ok": True, "x": cx, "y": cy, "clamped": clamped}


def double_click(x, y):
    _, win32con, _ = _win32()
    cx, cy, clamped = _mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,
                                   win32con.MOUSEEVENTF_LEFTUP, x, y)
    time.sleep(0.08)
    _mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,
                 win32con.MOUSEEVENTF_LEFTUP, cx, cy)
    return {"ok": True, "x": cx, "y": cy, "clamped": clamped}


def _key_event(vk, down):
    win32api, win32con, _ = _win32()
    flag = 0 if down else win32con.KEYEVENTF_KEYUP
    win32api.keybd_event(vk, 0, flag, 0)


def _mod_vk(mod):
    return {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}[mod]


def press_key(name):
    vk, mods = parse_key(name)
    for m in ("ctrl", "alt", "shift"):
        if m in mods:
            _key_event(_mod_vk(m), True)
    try:
        _key_event(vk, True)
        time.sleep(0.05)
        _key_event(vk, False)
    finally:
        for m in ("shift", "alt", "ctrl"):
            if m in mods:
                _key_event(_mod_vk(m), False)
    return {"ok": True, "key": name, "vk": vk, "modifiers": sorted(mods)}


def _type_ascii(text):
    """纯 ASCII 走键盘事件，大小写靠 Shift。"""
    win32api, _, _ = _win32()
    for ch in text:
        # VkKeyScan returns one int: low byte = virtual-key code,
        # high byte = shift state (1 = Shift). It is NOT a tuple.
        result = win32api.VkKeyScan(ch)
        vk = result & 0xFF
        shift = (result >> 8) & 1
        if shift:
            _key_event(0x10, True)
        _key_event(vk, True)
        time.sleep(0.01)
        _key_event(vk, False)
        if shift:
            _key_event(0x10, False)
        time.sleep(0.01)


def _type_via_clipboard(text):
    """非 ASCII（中文等）走剪贴板粘贴：先把文字放进剪贴板，再 Ctrl+V。"""
    try:
        import win32clipboard
    except ImportError:
        raise RuntimeError("中文打字需要 pywin32 的剪贴板支持，请先运行 install_windows.bat。")
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    press_key("Ctrl+V")
    time.sleep(0.1)


def type_text(text):
    """打字。text 必须是 str，超过 2000 字拒绝。"""
    if not isinstance(text, str):
        return {"ok": False, "error": "type_text 的文字必须是字符串"}
    if len(text) == 0:
        return {"ok": False, "error": "type_text 的文字不能为空"}
    if len(text) > MAX_TYPE_CHARS:
        return {"ok": False, "error": "一次最多打 %d 字，这次 %d 字，拒绝" % (MAX_TYPE_CHARS, len(text))}
    try:
        if is_ascii_text(text):
            _type_ascii(text)
            method = "keyboard"
        else:
            _type_via_clipboard(text)
            method = "clipboard"
    except Exception as e:
        return {"ok": False, "error": "打字失败: %s" % e}
    return {"ok": True, "chars": len(text), "method": method,
            "preview": log_preview(text)}
