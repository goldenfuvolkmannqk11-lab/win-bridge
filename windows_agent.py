#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge Windows agent v0.1（跑在 Matsuda 的 Windows 电脑上）
Python 3.10+，标准库 + pywin32 + Pillow（只在需要时用到）。

安全原则：
  - 只向外连（轮询中转服务），绝不对外开端口。
  - 只做白名单里的事：白名单文件夹内的文件读写、白名单文件夹内的 .py 脚本。
  - 没有任意命令执行，没有 shell。run_python 只跑指定的 .py 文件。
  - 每个任务都记审计日志 audit.log。
  - 杀开关：agent 文件夹里出现 STOP.txt，立即停止。
  - 安全模块 security.py：禁扩名、文本特征扫描、脚本哈希白名单、
    隔离区、可疑新文件扫描、Defender 扫描。安全事件记 security.log。
    注意：这层防御不能替代 Windows Defender / 杀毒软件。
"""

import json
import os
import subprocess
import sys
import time
import traceback
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import security  # noqa: E402

CONFIG_FILE = os.path.join(BASE_DIR, "bridge_config.json")
AUDIT_FILE = os.path.join(BASE_DIR, "audit.log")
STOP_FILE = os.path.join(BASE_DIR, "STOP.txt")
HASHES_FILE = os.path.join(BASE_DIR, "script_hashes.json")
GUI_ENABLED_FILE = os.path.join(BASE_DIR, "gui_enabled.txt")
MAX_BASE64_BYTES = 700 * 1024  # 回传 base64 的文件上限（中转任务体上限 1MB，留余量）


def gui_enabled():
    """GUI 控制开关：只有 bridge 文件夹里有 gui_enabled.txt 才允许。

    Matsuda 自己建这个文件表示“我同意这台电脑可以被点鼠标/打字”，
    删掉它就立刻停用，不用重启 agent。"""
    return os.path.isfile(GUI_ENABLED_FILE)


def gui_guard():
    """GUI 任务进门检查。返回 None = 通过，否则返回错误 dict。"""
    if not gui_enabled():
        security.security_log(BASE_DIR, "GUI_BLOCKED",
                              "GUI 任务被拒：gui_enabled.txt 不存在（未 opt-in）")
        return {"ok": False,
                "error": "GUI 控制未启用：请先在 C:\\muse-bridge 里新建一个空文件 "
                         "gui_enabled.txt（表示你同意这台电脑可以被点鼠标/打字），"
                         "删掉它就立刻停用。"}
    return None


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg


def audit(job_type, path, result_ok, note=""):
    line = "%s | %s | %s | ok=%s | %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), job_type, path, result_ok, note[:200])
    try:
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def is_allowed(path, allowed_folders):
    """路径必须落在白名单文件夹内，.. 穿透会被 normpath 后挡住。"""
    try:
        norm = os.path.normpath(os.path.abspath(path))
    except Exception:
        return False
    for folder in allowed_folders:
        base = os.path.normpath(os.path.abspath(folder))
        if norm == base or norm.startswith(base + os.sep):
            return True
    return False


def http_get(url, token, timeout=10):
    req = urllib.request.Request(url + ("&" if "?" in url else "?") + "token=" + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(url, payload, timeout=15):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------- 任务执行 ----------------

def do_list_dir(params, cfg):
    path = params.get("path", "")
    if not is_allowed(path, cfg["allowed_folders"]):
        return {"ok": False, "error": "路径不在白名单内: " + path}
    if not os.path.isdir(path):
        return {"ok": False, "error": "文件夹不存在: " + path}
    items = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        items.append({"name": name, "is_dir": os.path.isdir(full),
                      "size": os.path.getsize(full) if os.path.isfile(full) else 0})
    return {"ok": True, "path": path, "items": items}


def do_read_file(params, cfg):
    path = params.get("path", "")
    if not is_allowed(path, cfg["allowed_folders"]):
        return {"ok": False, "error": "路径不在白名单内: " + path}
    if not os.path.isfile(path):
        return {"ok": False, "error": "文件不存在: " + path}
    if os.path.getsize(path) > 5 * 1024 * 1024:
        return {"ok": False, "error": "文件超过 5MB，不读取: " + path}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return {"ok": True, "path": path, "content": f.read()}


def quarantine_dir(cfg):
    """隔离区：白名单第一个文件夹下的 quarantine/。"""
    return os.path.join(cfg["allowed_folders"][0], "quarantine")


def defender_scan(path):
    """用 Windows Defender 扫一个文件。

    找 MpCmdRun.exe，找到就跑 `MpCmdRun -Scan -ScanType 3 -File <path>`，
    超时 120 秒，返回 (found, exit_code, note)。
    Defender 返回 0 = 没发现威胁，2 = 发现威胁并处理。
    找不到 Defender 时返回 (False, None, ...)，绝不崩溃。
    """
    candidates = []
    for root in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 r"C:\Program Files"):
        if root:
            candidates.append(os.path.join(
                root, "Windows Defender", "MpCmdRun.exe"))
    mp = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not mp:
        return False, None, "Defender 未找到（MpCmdRun.exe 不存在），已跳过扫描"
    try:
        proc = subprocess.run(
            [mp, "-Scan", "-ScanType", "3", "-File", path],
            capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            note = "Defender 扫描通过（exit 0，未发现威胁）"
        elif proc.returncode == 2:
            note = "Defender 发现威胁并已处理（exit 2），请勿运行此文件"
        else:
            note = "Defender 扫描返回 exit %d，输出：%s" % (
                proc.returncode, (proc.stdout + proc.stderr)[-300:])
        return True, proc.returncode, note
    except subprocess.TimeoutExpired:
        return True, None, "Defender 扫描超时（120 秒），未确认安全"
    except Exception as e:
        return True, None, "Defender 扫描出错: %s" % e


def scan_work_folder(cfg, snapshot):
    """扫白名单文件夹里新出现 / 被改过的文件。

    snapshot 是 {绝对路径: sha256}，由主循环持有。每次调用只处理
    新增或哈希变了的文件：禁扩名或高危特征的移入 quarantine/，
    记 security.log；干净的只更新快照。跳过 quarantine/、*.log、
    script_hashes.json 和 agent 自己的文件。
    返回 (新发现数, 隔离数)。
    """
    folder = cfg["allowed_folders"][0]
    qdir = quarantine_dir(cfg)
    found, quarantined = 0, 0
    if not os.path.isdir(folder):
        return found, quarantined
    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        if name.endswith(".log") or name in ("script_hashes.json", "STOP.txt"):
            continue
        if os.path.abspath(full).startswith(os.path.abspath(qdir) + os.sep):
            continue
        try:
            digest = security.sha256_file(full)
        except Exception:
            continue
        if snapshot.get(os.path.abspath(full)) == digest:
            continue
        found += 1
        snapshot[os.path.abspath(full)] = digest
        ok, reasons = security.scan_file(full)
        if not ok:
            try:
                dest = security.quarantine(full, qdir)
            except Exception as e:
                security.security_log(BASE_DIR, "QUARANTINE_FAILED",
                                      "%s 隔离失败: %s" % (full, e))
                continue
            quarantined += 1
            security.security_log(
                BASE_DIR, "QUARANTINED",
                "%s -> %s，原因：%s" % (full, dest, "; ".join(reasons)))
        elif reasons:
            security.security_log(
                BASE_DIR, "SUSPICIOUS_FILE",
                "%s 可疑特征（未隔离，仅记录）：%s" % (full, "; ".join(reasons)))
    return found, quarantined


def do_write_file(params, cfg):
    path = params.get("path", "")
    content = params.get("content", "")
    if not is_allowed(path, cfg["allowed_folders"]):
        return {"ok": False, "error": "路径不在白名单内: " + path}
    if security.is_blocked_filename(path):
        security.security_log(BASE_DIR, "BLOCKED_WRITE",
                              "拒绝写入禁扩名文件: " + path)
        return {"ok": False, "error": "拒绝写入：禁止的扩展名 %s（安全策略）"
                % os.path.splitext(path)[1]}
    if len(content.encode("utf-8")) > security.MAX_PAYLOAD_BYTES:
        return {"ok": False, "error": "写入内容超过 1MB，拒绝"}
    reasons = security.scan_text(content)
    high = security.is_high_risk(reasons)
    if high:
        security.security_log(BASE_DIR, "BLOCKED_WRITE",
                              "写入内容命中高危特征，拒绝: %s，原因：%s"
                              % (path, "; ".join(high)))
        return {"ok": False, "error": "拒绝写入：内容命中高危特征："
                + "; ".join(high)}
    if reasons:
        security.security_log(BASE_DIR, "SUSPICIOUS_WRITE",
                              "写入内容有可疑特征（仍写入，仅记录）: %s，原因：%s"
                              % (path, "; ".join(reasons)))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}


def do_run_python(params, cfg):
    path = params.get("path", "")
    timeout = int(params.get("timeout", 120))
    if not is_allowed(path, cfg["allowed_folders"]):
        return {"ok": False, "error": "路径不在白名单内: " + path}
    if not path.lower().endswith(".py"):
        return {"ok": False, "error": "只允许运行 .py 文件: " + path}
    if not os.path.isfile(path):
        return {"ok": False, "error": "脚本不存在: " + path}
    # 安全检查 1：文件扫描（禁扩名已在上面挡住，这里扫高危文本特征）
    ok, reasons = security.scan_file(path)
    if not ok:
        try:
            dest = security.quarantine(path, quarantine_dir(cfg))
        except Exception:
            dest = path + "（隔离失败）"
        security.security_log(BASE_DIR, "BLOCKED_RUN",
                              "拒绝运行并隔离: %s -> %s，原因：%s"
                              % (path, dest, "; ".join(reasons)))
        return {"ok": False, "error": "拒绝运行：脚本命中高危特征，已隔离到 %s：%s"
                % (dest, "; ".join(reasons))}
    if reasons:
        security.security_log(BASE_DIR, "SUSPICIOUS_RUN",
                              "运行脚本有可疑特征（仍运行，仅记录）: %s，原因：%s"
                              % (path, "; ".join(reasons)))
    # 安全检查 2：脚本哈希白名单 —— 哈希变了就拒绝，等用户批准
    hash_ok, status, hash_reasons = security.check_script_hash(path, HASHES_FILE)
    if not hash_ok:
        security.security_log(BASE_DIR, "HASH_CHANGED",
                              "%s，原因：%s" % (path, "; ".join(hash_reasons)))
        return {"ok": False, "error": "; ".join(hash_reasons)}
    if status == "first":
        security.security_log(BASE_DIR, "HASH_FIRST_RUN",
                              "%s，原因：%s" % (path, "; ".join(hash_reasons)))
        # 安全检查 3：首次运行的脚本，有 Defender 就让它先扫一遍
        found, code, note = defender_scan(path)
        security.security_log(BASE_DIR, "DEFENDER_SCAN", "%s：%s" % (path, note))
        if found and code == 2:
            return {"ok": False, "error": "Defender 发现威胁，已拒绝运行: " + path}
    try:
        proc = subprocess.run(
            [sys.executable, path],
            capture_output=True, text=True, timeout=timeout,
            cwd=os.path.dirname(os.path.abspath(path)))
        return {"ok": proc.returncode == 0, "returncode": proc.returncode,
                "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "脚本运行超时（%d 秒）: %s" % (timeout, path)}


def do_run_zwcad_script(params, cfg):
    """跑一个驱动中望的 .py 脚本。先确认中望已打开，否则返回中文错误，不崩溃。"""
    try:
        import win32com.client
        try:
            win32com.client.GetActiveObject("ZWCAD.Application")
        except Exception:
            return {"ok": False,
                    "error": "中望没有打开：请先在 Windows 上打开中望（开一张空白图别关），再发这个任务。"}
    except ImportError:
        return {"ok": False, "error": "缺少 pywin32，请先运行 install_windows.bat 安装依赖。"}
    return do_run_python(params, cfg)


def do_screenshot(params, cfg):
    """v0.3：用 screen.py 截屏，图片缩到最长边 1280 存 JPG。

    除了返回本地路径，还把图片 base64 一起回传（<=700KB 才带，
    太大就不带，只留本地文件），这样远端不用再跑一次下载就能看到图。
    """
    blocked = gui_guard()
    if blocked:
        return blocked
    try:
        import screen
    except ImportError as e:
        return {"ok": False, "error": "读不到 screen.py: %s" % e}
    folder = cfg["allowed_folders"][0]
    result = screen.screenshot(folder, window=params.get("window") or None)
    if not result.get("ok"):
        return result
    if result.get("size_bytes", 0) <= MAX_BASE64_BYTES:
        try:
            import base64
            with open(result["path"], "rb") as f:
                result["image_base64"] = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            result["image_base64"] = None
            result["note"] = "图片回传失败（本地文件还在）: %s" % e
    else:
        result["note"] = "图片 %d 字节太大，没随结果回传；请用 read_file_base64 分段取，或直接在电脑上看 %s" % (
            result["size_bytes"], result["path"])
    audit("screenshot", result["path"], True,
          "window=%s %dx%d" % (result.get("window", ""), result["width"], result["height"]))
    return result


def do_read_file_base64(params, cfg):
    """读一个白名单内的二进制文件（截屏 JPG 等），base64 回传。<=700KB 才给。"""
    path = params.get("path", "")
    if not is_allowed(path, cfg["allowed_folders"]):
        return {"ok": False, "error": "路径不在白名单内: " + path}
    if not os.path.isfile(path):
        return {"ok": False, "error": "文件不存在: " + path}
    size = os.path.getsize(path)
    if size > MAX_BASE64_BYTES:
        return {"ok": False, "error": "文件 %d 字节，超过 base64 回传上限 %d 字节" % (size, MAX_BASE64_BYTES)}
    try:
        import base64
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        return {"ok": False, "error": "读取失败: %s" % e}
    return {"ok": True, "path": path, "size_bytes": size, "data_base64": data}


def _gui_handler(fn_name, params, cfg):
    """GUI 任务通用入口：先过开关，再调 screen.py 的同名函数。"""
    blocked = gui_guard()
    if blocked:
        return blocked
    try:
        import screen
    except ImportError as e:
        return {"ok": False, "error": "读不到 screen.py: %s" % e}
    fn = getattr(screen, fn_name, None)
    if fn is None:
        return {"ok": False, "error": "screen.py 里没有 %s" % fn_name}
    try:
        return fn(**params)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except RuntimeError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": "GUI 操作失败: %s" % e}


def do_click(params, cfg):
    if "x" not in params or "y" not in params:
        return {"ok": False, "error": "click 需要 x 和 y 两个数字（屏幕像素坐标）"}
    result = _gui_handler("click", {"x": params["x"], "y": params["y"]}, cfg)
    audit("click", "", result.get("ok", False), "x=%s y=%s" % (params["x"], params["y"]))
    return result


def do_double_click(params, cfg):
    if "x" not in params or "y" not in params:
        return {"ok": False, "error": "double_click 需要 x 和 y 两个数字（屏幕像素坐标）"}
    result = _gui_handler("double_click", {"x": params["x"], "y": params["y"]}, cfg)
    audit("double_click", "", result.get("ok", False), "x=%s y=%s" % (params["x"], params["y"]))
    return result


def do_type_text(params, cfg):
    text = params.get("text", "")
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "type_text 需要 text 文字（不能为空）"}
    result = _gui_handler("type_text", {"text": text}, cfg)
    try:
        import screen
        preview = screen.log_preview(text)
    except Exception:
        preview = "(%d chars)" % len(text)
    audit("type_text", "", result.get("ok", False), preview)
    return result


def do_press_key(params, cfg):
    key = params.get("key", "")
    if not isinstance(key, str) or not key:
        return {"ok": False, "error": "press_key 需要 key 按键名，例如 Enter / Esc / Ctrl+S"}
    result = _gui_handler("press_key", {"key": key}, cfg)
    audit("press_key", "", result.get("ok", False), "key=%s" % key)
    return result


def do_get_window(params, cfg):
    blocked = gui_guard()
    if blocked:
        return blocked
    try:
        import screen
    except ImportError as e:
        return {"ok": False, "error": "读不到 screen.py: %s" % e}
    try:
        result = screen.get_foreground_window()
    except Exception as e:
        return {"ok": False, "error": "取前台窗口失败: %s" % e}
    audit("get_window", "", result.get("ok", False), "title=%s" % result.get("title", ""))
    return result


HANDLERS = {
    "list_dir": do_list_dir,
    "read_file": do_read_file,
    "read_file_base64": do_read_file_base64,
    "write_file": do_write_file,
    "run_python": do_run_python,
    "run_zwcad_script": do_run_zwcad_script,
    "screenshot": do_screenshot,
    "click": do_click,
    "double_click": do_double_click,
    "type_text": do_type_text,
    "press_key": do_press_key,
    "get_window": do_get_window,
}


def execute_job(job, cfg):
    job_type = job.get("type", "")
    params = job.get("params", {}) or {}
    handler = HANDLERS.get(job_type)
    if not handler:
        return {"ok": False, "error": "不支持的任务类型: " + job_type}
    try:
        result = handler(params, cfg)
    except Exception as e:
        result = {"ok": False, "error": "执行出错: %s\n%s" % (e, traceback.format_exc()[-1000:])}
    audit(job_type, params.get("path", ""), result.get("ok", False),
          str(result.get("error", ""))[:200])
    return result


def main():
    print("muse-bridge Windows agent v0.3")
    try:
        cfg = load_config()
    except Exception as e:
        print("读不到 bridge_config.json: %s" % e)
        print("请先复制 bridge_config.json，填好 relay_url 和 token。")
        return
    relay = cfg["relay_url"].rstrip("/")
    token = cfg["token"]
    interval = float(cfg.get("poll_interval_seconds", 2))
    if token == "CHANGE_ME":
        print("警告: token 还是默认的 CHANGE_ME，请先改掉，否则中转会拒绝。")
    print("中转: %s  轮询间隔: %s 秒  白名单: %s" % (relay, interval, cfg["allowed_folders"]))
    print("杀开关: 在本文件夹新建 STOP.txt 即可立即停止 agent。Ctrl+C 也可以停。")
    print("安全: security.log 记录安全事件；可疑文件隔离到 %s" % quarantine_dir(cfg))
    print("GUI v0.3: %s（删掉 gui_enabled.txt 立刻停用）"
          % ("已启用" if gui_enabled() else "未启用，请在 bridge 文件夹新建 gui_enabled.txt 开启"))
    snapshot = {}
    while True:
        if os.path.exists(STOP_FILE):
            print("检测到 STOP.txt，agent 已停止。删掉 STOP.txt 后重新启动。")
            return
        try:
            found, quarantined = scan_work_folder(cfg, snapshot)
            if quarantined:
                print("安全：发现 %d 个新文件，隔离了 %d 个可疑文件（见 security.log）"
                      % (found, quarantined))
            elif found:
                print("安全：工作文件夹新增 %d 个文件，均干净" % found)
        except Exception as e:
            print("工作文件夹安全扫描出错: %s" % e)
        try:
            data = http_get(relay + "/job", token)
            job = data.get("job")
            if job:
                print("[%s] 收到任务 %s (%s)" % (time.strftime("%H:%M:%S"), job["id"], job["type"]))
                result = execute_job(job, cfg)
                http_post(relay + "/result", {"token": token, "id": job["id"], "result": result})
                print("  结果已回传，ok=%s" % result.get("ok"))
        except urllib.error.URLError as e:
            print("连不上中转 (%s)，%s 秒后重试" % (e, interval))
        except Exception as e:
            print("轮询出错: %s" % e)
        time.sleep(interval)


if __name__ == "__main__":
    main()
