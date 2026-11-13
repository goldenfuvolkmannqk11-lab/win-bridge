#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge 安全测试（Linux 上跑，不用 Windows）。

测 5 个场景：
  (a) 经中转提交 .exe 写入任务 → 被拒绝
  (b) 带可疑特征的脚本（powershell -enc）→ 被标记并隔离，不运行
  (c) 脚本改过之后哈希变了 → 拒绝运行；批准新哈希后恢复
  (d) 连续 5 次错口令 → 锁定 10 分钟（第 6 次连正确口令也进不来）
  (e) 干净的 .py 脚本 → 正常放行并运行

用法：python3 test_security.py，全部通过退出码 0。
注意：(d) 会在临时目录里产生锁定状态，测完即删，不影响正式的 lockout.json。
"""

import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import security  # noqa: E402

TMP = tempfile.mkdtemp(prefix="bridge_security_test_")
WORK = os.path.join(TMP, "work")
os.makedirs(WORK, exist_ok=True)
CFG = {"allowed_folders": [WORK], "poll_interval_seconds": 2}

import windows_agent  # noqa: E402
windows_agent.HASHES_FILE = os.path.join(TMP, "script_hashes.json")


def check(name, cond, extra=""):
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))
    if not cond:
        raise SystemExit(1)


def write_work(name, content):
    path = os.path.join(WORK, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------- (a) 中转拒绝 .exe 写入 ----------
print("== (a) .exe 写入任务经中转被拒绝 ==")
os.environ["BRIDGE_TOKEN"] = "test-token-123"
import relay_server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

relay_server.JOBS_FILE = os.path.join(TMP, "jobs.json")
relay_server.LOCKOUT_FILE = os.path.join(TMP, "lockout.json")
relay_server.LOCKOUT = security.TokenLockout(relay_server.LOCKOUT_FILE)
server = ThreadingHTTPServer(("127.0.0.1", 0), relay_server.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % port


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


status, resp = post("/job", {"token": "test-token-123", "type": "write_file",
                             "params": {"path": os.path.join(WORK, "evil.exe"),
                                        "content": "MZ..."}})
check("(a) .exe 写入任务被中转拒绝", status == 400 and not resp.get("ok"),
      "status=%d %s" % (status, resp.get("error", "")))

# 顺带：干净的 write_file 任务应被接受（只测中转收不收，不测 agent 执行）
status, resp = post("/job", {"token": "test-token-123", "type": "write_file",
                             "params": {"path": os.path.join(WORK, "note.txt"),
                                        "content": "hello"}})
check("(a) 干净的 write_file 任务被中转接受", status == 200 and resp.get("ok"),
      "status=%d" % status)

# ---------- (b) 可疑脚本被隔离，不运行 ----------
print("== (b) 可疑脚本被标记并隔离 ==")
evil = write_work("evil.py",
                  "import subprocess\n"
                  "subprocess.run(['powershell', '-enc', 'aGVsbG8='])\n")
result = windows_agent.do_run_python({"path": evil}, CFG)
check("(b) 可疑脚本拒绝运行", not result.get("ok"),
      "error=%s" % str(result.get("error", ""))[:120])
check("(b) 可疑脚本已移入隔离区", not os.path.exists(evil)
      and os.path.exists(os.path.join(WORK, "quarantine", "evil.py")),
      "原文件已不在，隔离区里有")
with open(os.path.join(BASE_DIR, "security.log"), encoding="utf-8") as f:
    log = f.read()
check("(b) security.log 记了 BLOCKED_RUN", "BLOCKED_RUN" in log)

# ---------- (e) 干净脚本放行并运行（放 (c) 之前，先证明干净脚本能跑） ----------
print("== (e) 干净脚本正常放行 ==")
clean = write_work("clean.py", "print('hello from clean script')\n")
result = windows_agent.do_run_python({"path": clean}, CFG)
check("(e) 干净脚本运行成功", result.get("ok")
      and "hello from clean script" in result.get("stdout", ""),
      "stdout=%s" % result.get("stdout", "").strip())

# ---------- (c) 脚本哈希变了 → 拒绝；批准后恢复 ----------
print("== (c) 脚本哈希变化拒绝运行 ==")
with open(clean, "a", encoding="utf-8") as f:
    f.write("print('tampered')\n")
result = windows_agent.do_run_python({"path": clean}, CFG)
check("(c) 哈希变了拒绝运行", not result.get("ok")
      and "哈希变了" in str(result.get("error", "")),
      "error=%s" % str(result.get("error", ""))[:120])
digest = security.approve_script_hash(clean, windows_agent.HASHES_FILE)
check("(c) 批准新哈希后可运行",
      windows_agent.do_run_python({"path": clean}, CFG).get("ok"),
      "新哈希 %s..." % digest[:16])

# ---------- (d) 5 次错口令锁定 ----------
print("== (d) 连续错口令 5 次触发锁定 ==")
for i in range(5):
    status, _ = post("/job", {"token": "wrong-token", "type": "list_dir",
                              "params": {"path": WORK}})
    check("(d) 第 %d 次错口令返回 401" % (i + 1), status == 401, "status=%d" % status)
status, resp = post("/job", {"token": "test-token-123", "type": "list_dir",
                             "params": {"path": WORK}})
check("(d) 锁定后正确口令也被拒（429）", status == 429,
      "status=%d %s" % (status, resp.get("error", "")))

server.shutdown()
print("\n全部 5 个场景通过：(a) .exe 拒绝 (b) 可疑脚本隔离 (c) 哈希变化拒绝+批准恢复 "
      "(d) 错口令 5 次锁定 (e) 干净脚本放行。")
print("临时目录：%s（可删）" % TMP)
