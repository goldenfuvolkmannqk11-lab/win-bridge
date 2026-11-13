#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_relay.py — 中转服务的端到端自测（在 Linux 上跑，不用 Windows）。

流程：
  1. 在 localhost 随机端口启动 relay_server
  2. POST /job 提交一个 list_dir 任务
  3. 模拟 Windows agent：GET /job 轮询取任务
  4. 模拟执行：列出当前目录，POST /result 回传
  5. GET /result/<id> 查回结果，断言能拿到
  6. 再测一个：token 错误应被拒绝（401）
"""

import json
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用测试专用的 jobs 文件，避免污染正式的 jobs.json
import relay_server
relay_server.JOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs_test.json")
relay_server.TOKEN = "test-token-123"
if os.path.exists(relay_server.JOBS_FILE):
    os.remove(relay_server.JOBS_FILE)

PORT = 18765
BASE = "http://127.0.0.1:%d" % PORT


def post(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), relay_server.Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.3)
    try:
        # 1. 健康检查
        code, body = get("/health")
        assert code == 200 and body["ok"], body
        print("[1] /health OK:", body)

        # 2. 提交 list_dir 任务
        code, body = post("/job", {"token": "test-token-123", "type": "list_dir",
                                   "params": {"path": os.path.dirname(os.path.abspath(__file__))}})
        assert code == 200 and body["ok"], body
        job_id = body["id"]
        print("[2] 提交任务 OK，id =", job_id)

        # 3. token 错误应被拒绝
        try:
            post("/job", {"token": "wrong", "type": "list_dir", "params": {}})
            raise AssertionError("错误 token 居然通过了！")
        except urllib.error.HTTPError as e:
            assert e.code == 401
            print("[3] 错误 token 被拒绝，401 OK")

        # 4. 模拟 agent 轮询取任务
        code, body = get("/job?token=test-token-123")
        assert code == 200 and body["job"] and body["job"]["id"] == job_id, body
        print("[4] agent 轮询取到任务:", body["job"]["type"])

        # 5. 模拟执行并回传结果
        items = sorted(os.listdir(os.path.dirname(os.path.abspath(__file__))))
        code, body = post("/result", {"token": "test-token-123", "id": job_id,
                                      "result": {"ok": True, "items": items}})
        assert code == 200 and body["ok"], body
        print("[5] 回传结果 OK")

        # 6. 查回结果
        code, body = get("/result/%s?token=test-token-123" % job_id)
        assert code == 200 and body["status"] == "done", body
        assert "relay_server.py" in body["result"]["items"], body
        print("[6] 查回结果 OK，status=done，目录里有 relay_server.py")
        print("\n全部 6 步通过：中转服务的 提交→轮询→回传→查询 全链路正常。")
    finally:
        server.shutdown()
        if os.path.exists(relay_server.JOBS_FILE):
            os.remove(relay_server.JOBS_FILE)


if __name__ == "__main__":
    main()
