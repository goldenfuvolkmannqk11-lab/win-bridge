#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge v0.2 测试（test_send_job.py）
在 Linux 上跑：起一个临时中转（端口 18765）+ 真的 windows_agent.py，
测 send_job.py 一键发任务和 watch_folder.py 文件夹监听。
不跑中望（本机没有 Windows / 中望），run_zwcad_script 不测。

用法：python3 test_send_job.py
退出码 0 = 全部通过。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

SRC = os.path.dirname(os.path.abspath(__file__))
PORT = 18765
TOKEN = "testtoken123"
RELAY_URL = "http://127.0.0.1:%d" % PORT

PASSED = []
FAILED = []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name)
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))


def run(cmd, cwd, timeout=60, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout, env=e)
    return p


def wait_health(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(RELAY_URL + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    tmp = tempfile.mkdtemp(prefix="bridge_v02_test_")
    bridge = os.path.join(tmp, "bridge")
    work = os.path.join(bridge, "work")
    os.makedirs(work)
    for f in ("relay_server.py", "security.py", "windows_agent.py",
              "send_job.py", "watch_folder.py", "get_token.py"):
        shutil.copy(os.path.join(SRC, f), os.path.join(bridge, f))
    cfg = {"allowed_folders": [work], "poll_interval_seconds": 1,
           "relay_url": RELAY_URL, "token": TOKEN}
    with open(os.path.join(bridge, "bridge_config.json"), "w",
              encoding="utf-8") as f:
        json.dump(cfg, f)
    print("测试目录：%s" % tmp)

    relay = agent = None
    try:
        # --- 起中转 + 代理 ---
        relay = subprocess.Popen(
            [sys.executable, "relay_server.py"], cwd=bridge,
            env={**os.environ, "BRIDGE_TOKEN": TOKEN,
                 "BRIDGE_PORT": str(PORT)},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        check("中转启动，/health 通", wait_health())
        agent = subprocess.Popen(
            [sys.executable, "windows_agent.py"], cwd=bridge,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(2)  # 等代理开始轮询

        # --- (1) send_job list_dir ---
        p = run([sys.executable, "send_job.py", "list_dir"], cwd=bridge, timeout=30)
        check("send_job list_dir 退出码 0", p.returncode == 0, p.stdout.strip()[-200:])
        try:
            out = json.loads(p.stdout[p.stdout.index("{"):])
            check("send_job list_dir 结果 ok", out.get("ok") is True)
        except Exception as e:
            check("send_job list_dir 结果是 JSON", False, str(e))

        # --- (2) send_job run_python ---
        with open(os.path.join(work, "hello_test.py"), "w", encoding="utf-8") as f:
            f.write('print("hello-from-bridge")\n')
        time.sleep(3)  # 让 agent 的文件夹扫描先看到它（只记快照，不影响）
        p = run([sys.executable, "send_job.py", "run_python", "hello_test.py"],
                cwd=bridge, timeout=60)
        check("send_job run_python 退出码 0", p.returncode == 0,
              p.stdout.strip()[-300:])
        check("send_job run_python 输出里有 hello",
              "hello-from-bridge" in p.stdout)

        # --- (3) 错口令 → 401 中文提示，退出码 1 ---
        p = run([sys.executable, "send_job.py", "list_dir",
                 "--token", "wrongtoken"], cwd=bridge, timeout=30)
        check("错口令退出码 1", p.returncode == 1)
        check("错口令提示是中文 401", "token 不对" in p.stdout, p.stdout.strip()[-200:])

        # --- (4) get_token.py ---
        p = run([sys.executable, "get_token.py"], cwd=bridge, timeout=10)
        check("get_token.py 打印出 token", p.stdout.strip() == TOKEN,
              repr(p.stdout.strip()))

        # --- (5) watcher：丢一个新 .py，自动跑，结果写 .result.json ---
        with open(os.path.join(work, "watcher_test.py"), "w", encoding="utf-8") as f:
            f.write('print("watcher-ok")\n')
        p = run([sys.executable, "watch_folder.py", "--once"], cwd=bridge, timeout=60)
        res_path = os.path.join(work, "watcher_test.result.json")
        ok = os.path.isfile(res_path)
        check("watcher 写出 watcher_test.result.json", ok)
        if ok:
            with open(res_path, encoding="utf-8") as f:
                res = json.load(f)
            check("watcher 结果 ok", res.get("ok") is True
                  and res.get("result", {}).get("ok") is True, str(res)[:200])
            check("watcher 输出里有 watcher-ok",
                  "watcher-ok" in json.dumps(res, ensure_ascii=False))
        mtime1 = os.path.getmtime(res_path) if ok else 0

        # --- (6) watcher 不重复跑同一个文件 ---
        time.sleep(1)
        p = run([sys.executable, "watch_folder.py", "--once"], cwd=bridge, timeout=60)
        mtime2 = os.path.getmtime(res_path) if ok else 0
        check("watcher 第二次不重复跑（result 文件没被重写）", mtime1 == mtime2)

        # --- (7) watcher：.job.json 按里面写的类型发任务 ---
        with open(os.path.join(work, "list_test.job.json"), "w", encoding="utf-8") as f:
            json.dump({"type": "list_dir", "params": {"path": "."}}, f)
        p = run([sys.executable, "watch_folder.py", "--once"], cwd=bridge, timeout=60)
        res_path = os.path.join(work, "list_test.result.json")
        ok = os.path.isfile(res_path)
        check("watcher 写出 list_test.result.json", ok)
        if ok:
            with open(res_path, encoding="utf-8") as f:
                res = json.load(f)
            check("watcher .job.json 结果 ok",
                  res.get("ok") is True and res.get("result", {}).get("ok") is True,
                  str(res)[:200])

        # --- (8) watcher：高危脚本拒绝并隔离（先停 agent，避免它先抢走文件） ---
        agent.terminate()
        try:
            agent.wait(timeout=5)
        except Exception:
            agent.kill()
        agent = None
        with open(os.path.join(work, "evil_test.py"), "w", encoding="utf-8") as f:
            f.write('import os\nos.system("powershell -enc AAAA")\n')
        p = run([sys.executable, "watch_folder.py", "--once"], cwd=bridge, timeout=60)
        quarantined = os.path.join(work, "quarantine", "evil_test.py")
        res_path = os.path.join(work, "evil_test.result.json")
        check("watcher 高危脚本被隔离到 quarantine/",
              os.path.isfile(quarantined), p.stdout.strip()[-200:])
        check("watcher 高危脚本结果 ok=False",
              os.path.isfile(res_path)
              and json.load(open(res_path, encoding="utf-8")).get("ok") is False)
    finally:
        for proc in (agent, relay):
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n通过 %d 个，失败 %d 个：%s"
          % (len(PASSED), len(FAILED), "；".join(FAILED) if FAILED else "无"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
