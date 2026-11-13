#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge v0.2 一键发任务（send_job.py）
Windows / Linux 都能跑，标准库 only。

用法（在 C:\\muse-bridge 那个文件夹里跑）：
  python send_job.py list_dir [路径]              列文件夹（默认是工作文件夹）
  python send_job.py read_file <文件>             读文件
  python send_job.py write_file <文件> <内容>     写文件
  python send_job.py run_python <脚本.py>         跑一个 Python 脚本
  python send_job.py run_zwcad_script <脚本.py>   跑一个中望脚本（中望要先打开）
  python send_job.py screenshot                   截屏（图片存到当前文件夹）
  python send_job.py click <x> <y>               v0.3 点一下屏幕坐标（需 gui_enabled.txt）
  python send_job.py double_click <x> <y>        v0.3 点两下
  python send_job.py type_text <文字>            v0.3 打字（中文走剪贴板粘贴，别输密码）
  python send_job.py type_text hello --delay 5   打字前等 5 秒，够你切换到记事本再打
  python send_job.py click 960 540 --delay 5      点鼠标前等 5 秒，够你切换窗口
  python send_job.py press_key <按键>            v0.3 按一个键，如 Enter / Esc / Ctrl+S
  python send_job.py get_window                  v0.3 看当前前台窗口标题和大小
  python send_job.py read_file_base64 <文件>     v0.3 下载一个二进制文件（截屏 JPG 等）
  选项：--timeout 秒数（默认 120），--token 口令（仅测试用，正常从配置文件读）

它做的事：读 bridge_config.json 里的 relay_url 和 token → POST /job 发任务
→ 每 1 秒查一次 GET /result/<id>，最多等 120 秒 → 把结果打出来。
不用再手敲 curl，不用再复制 id。

退出码：0 = 任务成功（result.ok 是 true）；1 = 任务失败、连不上中转、
401、429 或等超时；2 = 用法不对。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "bridge_config.json")

JOB_TYPES = ("list_dir", "read_file", "write_file",
             "run_python", "run_zwcad_script", "screenshot",
             "click", "double_click", "type_text", "press_key",
             "get_window", "read_file_base64")
PATH_TYPES = ("list_dir", "read_file", "run_python", "run_zwcad_script")


class BridgeError(Exception):
    """能直接报给用户看的错误（中文）。"""


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        raise BridgeError("读不到 bridge_config.json：%s\n"
                          "请确认 send_job.py 和 bridge_config.json 在同一个文件夹。"
                          % e)
    if not cfg.get("relay_url") or not cfg.get("token"):
        raise BridgeError("bridge_config.json 里没有 relay_url 或 token，"
                          "请先填好再发任务。")
    if cfg.get("token") == "CHANGE_ME":
        print("警告：token 还是默认的 CHANGE_ME，"
              "中转那边如果也用的默认口令能通，正式用请先改掉。")
    return cfg


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


def resolve_path(arg, cfg):
    """把命令行给的路径解析成绝对路径。

    相对路径（比如 flange_zwcad_v3.py）当成工作文件夹里的文件；
    绝对路径直接用，但必须在白名单内，否则拒绝。
    """
    folders = cfg.get("allowed_folders") or []
    if not folders:
        raise BridgeError("配置文件里没有 allowed_folders。")
    if os.path.isabs(arg):
        path = os.path.normpath(arg)
    else:
        path = os.path.normpath(os.path.join(folders[0], arg))
    if not is_allowed(path, folders):
        raise BridgeError("路径不在白名单内，拒绝发送：%s\n"
                          "白名单只有：%s" % (path, folders))
    return path


def print_json(obj):
    """把结果打出来。Windows cmd 默认是 GBK，直接打中文会崩，
    所以先按 UTF-8 写，写不出去再退回 ASCII 转义。"""
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    try:
        sys.stdout.flush()  # 先刷掉 print() 的缓冲，否则管道里顺序会乱
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        print(json.dumps(obj, ensure_ascii=True, indent=2))


def _http_json(method, url, token=None, payload=None, timeout=15):
    """发一次 HTTP，返回解析好的 JSON。401 / 429 翻译成中文报错。"""
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        sep = "&" if "?" in url else "?"
        url = url + sep + "token=" + urllib.parse.quote(token, safe="")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            detail = body.get("error", "")
        except Exception:
            detail = ""
        if e.code == 401:
            raise BridgeError("token 不对（401），请检查 bridge_config.json 里的 "
                              "token 是否和中转的 BRIDGE_TOKEN 一致（区分大小写）。%s"
                              % ("（中转说：" + detail + "）" if detail else ""))
        if e.code == 429:
            raise BridgeError("被锁定了（429）：口令连续错 5 次，中转锁定 10 分钟。\n"
                              "等 10 分钟再试，或者停掉中转、删掉 lockout.json 再重启。")
        raise BridgeError("中转返回 HTTP %s：%s" % (e.code, detail or "未知错误"))
    except urllib.error.URLError as e:
        raise BridgeError("连不上中转：%s\n"
                          "请确认中转在跑（双击 start_bridge.bat，或跑 python relay_server.py），"
                          "relay_url 是 %s。"
                          % (e.reason, _RELAY_URL[0] if _RELAY_URL else "？"))


_RELAY_URL = [""]


def submit_and_wait(relay, token, job_type, params, timeout=120):
    """发任务并等结果。返回 (job_id, status, result)。"""
    _RELAY_URL[0] = relay
    resp = _http_json("POST", relay + "/job",
                      payload={"token": token, "type": job_type, "params": params})
    if not resp.get("ok"):
        raise BridgeError("发任务被中转拒绝：%s" % resp.get("error", "未知错误"))
    job_id = resp["id"]
    print("任务已提交，id=%s，正在等结果（最多 %s 秒）..." % (job_id, timeout))
    deadline = time.time() + timeout
    last_status = "pending"
    while time.time() < deadline:
        time.sleep(1)
        resp = _http_json("GET", relay + "/result/" + job_id, token=token)
        if not resp.get("ok"):
            raise BridgeError("查结果失败：%s" % resp.get("error", "未知错误"))
        last_status = resp.get("status", "")
        if last_status == "done":
            return job_id, last_status, resp.get("result")
    raise BridgeError("等了 %s 秒任务还没完成（最后状态：%s）。"
                      "可能是 agent 停了，请看 agent 那个窗口还在不在跑。"
                      % (timeout, last_status))


def save_base64_file(result, default_name):
    """把结果里的 base64 存成本地文件。返回本地路径，失败返回 None。"""
    import base64
    data = result.get("image_base64") or result.get("data_base64")
    if not data:
        return None
    name = result.get("file") or os.path.basename(result.get("path", "")) or default_name
    local = os.path.join(os.getcwd(), name)
    try:
        with open(local, "wb") as f:
            f.write(base64.b64decode(data))
        return local
    except Exception as e:
        print("保存图片失败：%s" % e)
        return None


def usage():
    print(__doc__.strip())
    print("\n例子：")
    print("  python send_job.py list_dir")
    print("  python send_job.py run_zwcad_script flange_zwcad_v3.py")
    print("  python send_job.py run_python hello.py --timeout 30")
    print("  python send_job.py screenshot")
    print("  python send_job.py click 960 540")
    print("  python send_job.py type_text hello")
    print("  python send_job.py press_key Enter")


def main(argv):
    timeout = 120
    token_override = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--timeout" and i + 1 < len(argv):
            try:
                timeout = int(argv[i + 1])
            except ValueError:
                print("用法不对：--timeout 后面要跟秒数。")
                return 2
            i += 2
        elif argv[i] == "--token" and i + 1 < len(argv):
            token_override = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1
    if not args or args[0] not in JOB_TYPES:
        usage()
        return 2
    job_type = args[0]
    try:
        cfg = load_config()
    except BridgeError as e:
        print(e)
        return 1
    relay = cfg["relay_url"].rstrip("/")
    token = token_override if token_override is not None else cfg["token"]
    folders = cfg.get("allowed_folders") or []
    work = folders[0] if folders else ""

    # --delay N: 发任务前先等 N 秒，给你时间切换到目标窗口再打字/点鼠标。
    # 例: python send_job.py type_text hello --delay 5
    delay = 0
    if "--delay" in args:
        i = args.index("--delay")
        try:
            delay = int(args[i + 1])
        except Exception:
            print("用法不对：--delay 后面要跟秒数，如 --delay 5。")
            return 2
        del args[i:i + 2]
    if delay > 0:
        import time as _time
        print("等待 %d 秒，请切换到目标窗口（比如记事本）..." % delay)
        _time.sleep(delay)

    try:
        if job_type == "list_dir":
            path = resolve_path(args[1], cfg) if len(args) > 1 else work
            params = {"path": path}
        elif job_type in ("read_file", "run_python", "run_zwcad_script",
                          "read_file_base64"):
            if len(args) < 2:
                print("用法不对：%s 后面要跟一个文件。" % job_type)
                usage()
                return 2
            params = {"path": resolve_path(args[1], cfg)}
        elif job_type in ("click", "double_click"):
            if len(args) < 3:
                print("用法不对：%s 后面要跟 x y 两个数字。" % job_type)
                print("例如：python send_job.py click 960 540")
                return 2
            try:
                params = {"x": int(args[1]), "y": int(args[2])}
            except ValueError:
                print("用法不对：x y 必须是数字。")
                return 2
        elif job_type == "type_text":
            if len(args) < 2:
                print("用法不对：type_text 后面要跟文字。")
                print("例如：python send_job.py type_text hello")
                return 2
            params = {"text": " ".join(args[1:])}
        elif job_type == "press_key":
            if len(args) < 2:
                print("用法不对：press_key 后面要跟按键名，如 Enter / Esc / Ctrl+S。")
                return 2
            params = {"key": args[1]}
        elif job_type == "get_window":
            params = {}
        elif job_type == "write_file":
            if len(args) < 3:
                print("用法不对：write_file 后面要跟文件和内容。")
                print("例如：python send_job.py write_file hello.txt 你好")
                return 2
            params = {"path": resolve_path(args[1], cfg), "content": args[2]}
        elif job_type == "screenshot":
            params = {}
        else:  # 不会走到这里，上面已经拦住了
            params = {}
    except BridgeError as e:
        print(e)
        return 1

    try:
        job_id, status, result = submit_and_wait(relay, token, job_type,
                                                 params, timeout=timeout)
    except BridgeError as e:
        print(e)
        return 1
    print("任务完成，id=%s，状态=%s，结果：" % (job_id, status))
    if isinstance(result, dict) and job_type in ("screenshot", "read_file_base64"):
        # 图片 base64 太长不直接打出来：先存成本地文件，再把 base64 字段遮掉打印
        local = save_base64_file(result, "screenshot_%s.jpg" % job_id)
        if local:
            print("图片已存到本地：%s" % local)
        printable = {k: v for k, v in result.items()
                     if k not in ("image_base64", "data_base64")}
        if "image_base64" in result or "data_base64" in result:
            printable["base64"] = "<已存成本地文件，上面不重复打印>"
        print_json(printable)
    else:
        print_json(result if result is not None else {"ok": False, "error": "没有结果"})
    if isinstance(result, dict) and result.get("ok"):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
