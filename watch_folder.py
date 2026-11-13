#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge v0.2 文件夹监听（watch_folder.py）
Windows / Linux 都能跑，标准库 only。

用法（在 C:\\muse-bridge 那个文件夹里跑）：
  python watch_folder.py            一直盯着工作文件夹，有新文件就自动发任务
  python watch_folder.py --once     只扫一轮、处理完能处理的文件就退出（测试用）
  python watch_folder.py --interval 2   每 2 秒扫一轮（默认 1 秒）

规则：
  - 只盯白名单第一个文件夹（默认 C:\\muse-bridge\\work），不盯子文件夹，
    不盯 quarantine/ 隔离区。
  - 新出现的 .py 文件：先过 security.scan_file 检查，高危的直接隔离到
    quarantine/、不提交；干净的按名字决定类型——名字里带 "zwcad"
    （比如 flange_zwcad_v3.py）就发 run_zwcad_script，否则发 run_python。
  - 新出现的 .job.json 文件：里面是 {"type": "...", "params": {...}}，
    按里面写的类型发任务；params 里的相对路径按工作文件夹解析。
  - 文件要先“稳定”才处理：大小 2 秒没变才算写完了，避免拿到写一半的文件。
  - 同一个文件只跑一次：处理过的哈希记在 processed.json 里（在桥文件夹，
    不在工作文件夹），改过内容（哈希变了）才会再跑。
  - 每个文件处理完，在它旁边写一个 <名字>.result.json，里面是任务结果。
  - 在桥文件夹里新建 STOP.txt 就停下，Ctrl+C 也能停。
"""

import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import security  # noqa: E402
from send_job import (JOB_TYPES, load_config, resolve_path,  # noqa: E402
                      submit_and_wait, BridgeError)

CONFIG_STOP_FILE = os.path.join(BASE_DIR, "STOP.txt")
PROCESSED_FILE = os.path.join(BASE_DIR, "processed.json")
STABLE_SECONDS = 2.0

SKIP_NAMES = {"script_hashes.json", "STOP.txt", "processed.json"}
SKIP_SUFFIXES = (".result.json", ".log")


def print_line(text):
    """打印一行。Windows cmd 默认是 GBK，直接打中文会崩，
    所以先按 UTF-8 写，写不出去再退回 ASCII 转义。"""
    try:
        sys.stdout.flush()  # 先刷掉 print() 的缓冲，否则管道里顺序会乱
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()
    except Exception:
        try:
            print(text.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
        except Exception:
            print(text.encode("ascii", errors="replace").decode("ascii"))


def load_processed():
    try:
        with open(PROCESSED_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_processed(processed):
    tmp = PROCESSED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROCESSED_FILE)


def result_path_for(source_path):
    """foo.py -> foo.result.json；foo.job.json -> foo.result.json。"""
    name = os.path.basename(source_path)
    if name.lower().endswith(".job.json"):
        stem = name[:-len(".job.json")]
    else:
        stem = os.path.splitext(name)[0]
    return os.path.join(os.path.dirname(source_path), stem + ".result.json")


def write_result(source_path, payload):
    out = result_path_for(source_path)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out


def job_for_py(path):
    """按文件名决定 .py 文件的任务类型。"""
    if "zwcad" in os.path.basename(path).lower():
        return "run_zwcad_script", {"path": path}
    return "run_python", {"path": path}


def job_for_job_json(path, cfg):
    """读 .job.json，返回 (type, params)。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(".job.json 里不是一个 JSON 对象")
    job_type = data.get("type", "")
    if job_type not in JOB_TYPES:
        raise ValueError("不支持的任务类型：%s（只支持 %s）"
                         % (job_type, ", ".join(JOB_TYPES)))
    params = data.get("params", {}) or {}
    if not isinstance(params, dict):
        raise ValueError(".job.json 里的 params 不是一个对象")
    if "path" in params and isinstance(params["path"], str):
        params["path"] = resolve_path(params["path"], cfg)
    timeout = data.get("timeout", 120)
    try:
        timeout = int(timeout)
    except Exception:
        timeout = 120
    return job_type, params, timeout


def process_file(path, cfg, relay, token, processed):
    """处理一个已稳定的新文件。返回 True 表示处理完了（成功或已拒绝）。"""
    name = os.path.basename(path)
    digest = security.sha256_file(path)
    qdir = os.path.join(cfg["allowed_folders"][0], "quarantine")
    try:
        if name.lower().endswith(".job.json"):
            job_type, params, timeout = job_for_job_json(path, cfg)
        elif name.lower().endswith(".py"):
            ok, reasons = security.scan_file(path)
            if not ok:
                try:
                    dest = security.quarantine(path, qdir)
                except Exception as e:
                    dest = path + "（隔离失败）"
                security.security_log(BASE_DIR, "WATCHER_BLOCKED",
                                      "监听拒绝并隔离：%s -> %s，原因：%s"
                                      % (path, dest, "; ".join(reasons)))
                payload = {"ok": False, "error": "安全检查没过，已隔离到 %s：%s"
                           % (dest, "; ".join(reasons))}
                out = write_result(path, payload)
                print_line("[%s] %s 安全检查没过，已隔离，结果见 %s"
                           % (time.strftime("%H:%M:%S"), name,
                              os.path.basename(out)))
                processed[os.path.abspath(path)] = digest
                save_processed(processed)
                return True
            if reasons:
                security.security_log(BASE_DIR, "WATCHER_SUSPICIOUS",
                                      "%s 有可疑特征（仍提交，仅记录）：%s"
                                      % (path, "; ".join(reasons)))
            job_type, params = job_for_py(path)
            timeout = 120
        else:
            return False
    except Exception as e:
        payload = {"ok": False, "error": "监听处理 %s 出错：%s" % (name, e)}
        out = write_result(path, payload)
        print_line("[%s] %s 处理出错：%s，结果见 %s"
                   % (time.strftime("%H:%M:%S"), name, e, os.path.basename(out)))
        processed[os.path.abspath(path)] = digest
        save_processed(processed)
        return True

    print_line("[%s] 发现新文件 %s，正在发 %s 任务..."
               % (time.strftime("%H:%M:%S"), name, job_type))
    try:
        job_id, status, result = submit_and_wait(relay, token, job_type,
                                                 params, timeout=timeout)
        payload = {"ok": True, "job_id": job_id, "status": status,
                   "job_type": job_type, "result": result}
        ok = isinstance(result, dict) and result.get("ok")
    except BridgeError as e:
        payload = {"ok": False, "job_type": job_type, "error": str(e)}
        ok = False
    out = write_result(path, payload)
    print_line("[%s] %s 处理完，ok=%s，结果见 %s"
               % (time.strftime("%H:%M:%S"), name, ok, os.path.basename(out)))
    processed[os.path.abspath(path)] = digest
    save_processed(processed)
    return True


def scan_once(cfg, relay, token, processed, seen):
    """扫一轮工作文件夹。返回 (处理了几个文件， 还有几个没稳定/没处理的文件）。

    seen 是 {绝对路径: (大小, 修改时间, 首次看到的时间)}，用来判断文件稳不稳定。
    """
    work = cfg["allowed_folders"][0]
    qdir = os.path.abspath(os.path.join(work, "quarantine"))
    done = 0
    pending = 0
    if not os.path.isdir(work):
        print_line("工作文件夹不存在：%s" % work)
        return done, pending
    for name in sorted(os.listdir(work)):
        full = os.path.join(work, name)
        if not os.path.isfile(full):
            continue
        if os.path.abspath(full).startswith(qdir + os.sep):
            continue
        if name in SKIP_NAMES or name.lower().endswith(SKIP_SUFFIXES):
            continue
        if not (name.lower().endswith(".py") or name.lower().endswith(".job.json")):
            continue
        key = os.path.abspath(full)
        try:
            digest = security.sha256_file(full)
        except Exception:
            continue
        if processed.get(key) == digest:
            continue  # 处理过且没改过，不再跑
        try:
            st = os.stat(full)
        except Exception:
            continue
        now = time.time()
        prev = seen.get(key)
        if prev is None or prev[0] != st.st_size or prev[1] != st.st_mtime:
            seen[key] = (st.st_size, st.st_mtime, now)
            pending += 1
            continue
        if now - prev[2] < STABLE_SECONDS:
            pending += 1
            continue
        seen.pop(key, None)
        if process_file(full, cfg, relay, token, processed):
            done += 1
    return done, pending


def main(argv):
    once = "--once" in argv
    interval = 1.0
    for i, a in enumerate(argv):
        if a == "--interval" and i + 1 < len(argv):
            try:
                interval = float(argv[i + 1])
            except ValueError:
                pass
    try:
        cfg = load_config()
    except BridgeError as e:
        print_line(str(e))
        return 1
    relay = cfg["relay_url"].rstrip("/")
    token = cfg["token"]
    work = cfg["allowed_folders"][0]
    print_line("muse-bridge v0.2 文件夹监听")
    print_line("盯着：%s（只看 .py 和 .job.json，新文件稳定 %s 秒后自动发任务）"
               % (work, int(STABLE_SECONDS)))
    print_line("结果写在同名 .result.json 里；STOP.txt 或 Ctrl+C 停止。")
    processed = load_processed()
    seen = {}
    if once:
        # 只扫一轮：反复扫，直到没有没处理的文件（最多 30 秒），再退出。
        deadline = time.time() + 30
        while time.time() < deadline:
            if os.path.exists(CONFIG_STOP_FILE):
                print_line("检测到 STOP.txt，监听已停止。")
                return 0
            done, pending = scan_once(cfg, relay, token, processed, seen)
            if pending == 0:
                return 0
            time.sleep(1)
        print_line("30 秒还没稳定，退出（剩下的下次再跑）。")
        return 0
    while True:
        if os.path.exists(CONFIG_STOP_FILE):
            print_line("检测到 STOP.txt，监听已停止。删掉 STOP.txt 后重新启动。")
            return 0
        try:
            scan_once(cfg, relay, token, processed, seen)
        except Exception as e:
            print_line("扫文件夹出错：%s" % e)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print_line("已停止。")
        sys.exit(0)
