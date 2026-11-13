#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge 中转服务 v0.1（跑在远程 Linux 上）
Python 标准库 only，无额外依赖。

接口：
  GET  /health            健康检查
  POST /job               提交一个任务（JSON，带 token）
  GET  /job?token=...     Windows agent 轮询：取下一个待处理任务
  POST /result            agent 回传结果（JSON，带 token 和 id）
  GET  /result/<id>?token=...  查某个任务的结果

任务存在 jobs.json 里，重启不丢。没有数据库。

安全（security.py）：
  - 任务体超过 1MB 直接拒绝；write_file 任务的文件名带禁扩名直接拒绝。
  - 口令连续错 5 次锁定 10 分钟，安全事件记 security.log。
  - 这层防御不能替代杀毒软件 / 防火墙。
"""

import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import security

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, "jobs.json")
LOCKOUT_FILE = os.path.join(BASE_DIR, "lockout.json")
TOKEN = os.environ.get("BRIDGE_TOKEN", "CHANGE_ME")  # 与 bridge_config.json 里的 token 一致
HOST = os.environ.get("BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("BRIDGE_PORT", "8765"))
LOCKOUT = security.TokenLockout(LOCKOUT_FILE)


def load_jobs():
    if not os.path.exists(JOBS_FILE):
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_jobs(jobs):
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JOBS_FILE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静一点
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        """读请求体。超过 1MB 返回 (None, 错误信息)，不再继续读。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}, None
        if length > security.MAX_PAYLOAD_BYTES:
            # 必须把超大体读掉丢弃，否则连接会乱；这里直接关连接更简单，
            # 由调用方返回 413。
            return None, "任务体超过 1MB（%d 字节），拒绝" % length
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception:
            return {}, None

    def _auth(self, token):
        """口令校验 + 锁定。返回 (ok, 状态码, 错误信息)。"""
        locked, remaining = LOCKOUT.is_locked()
        if locked:
            return False, 429, "口令错误太多次，已锁定 %d 秒后再试" % remaining
        if token == TOKEN:
            LOCKOUT.record_success()
            return True, 200, ""
        triggered = LOCKOUT.record_failure()
        security.security_log(BASE_DIR, "BAD_TOKEN",
                              "口令错误，来源 %s" % self.client_address[0])
        if triggered:
            security.security_log(BASE_DIR, "LOCKOUT",
                                  "连续错口令 5 次，锁定 10 分钟，来源 %s"
                                  % self.client_address[0])
        return False, 401, "token 无效"

    def _require_auth(self, token):
        ok, code, msg = self._auth(token)
        if not ok:
            self._send(code, {"ok": False, "error": msg})
        return ok

    # ---------- GET ----------
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = qs.get("token", [""])[0]

        if parsed.path == "/health":
            self._send(200, {"ok": True, "service": "muse-bridge-relay", "version": "0.1"})
            return

        if parsed.path == "/job":
            if not self._require_auth(token):
                return
            jobs = load_jobs()
            # 取最早提交的 pending 任务
            pending = [j for j in jobs.values() if j.get("status") == "pending"]
            pending.sort(key=lambda j: j.get("created_at", 0))
            if not pending:
                self._send(200, {"ok": True, "job": None})
                return
            job = pending[0]
            job["status"] = "dispatched"
            job["dispatched_at"] = time.time()
            save_jobs(jobs)
            self._send(200, {"ok": True, "job": job})
            return

        if parsed.path.startswith("/result/"):
            if not self._require_auth(token):
                return
            job_id = parsed.path[len("/result/"):]
            jobs = load_jobs()
            job = jobs.get(job_id)
            if not job:
                self._send(404, {"ok": False, "error": "任务不存在: " + job_id})
                return
            self._send(200, {"ok": True, "id": job_id, "status": job.get("status"),
                             "result": job.get("result")})
            return

        self._send(404, {"ok": False, "error": "未知路径: " + parsed.path})

    # ---------- POST ----------
    def do_POST(self):
        parsed = urlparse(self.path)
        body, read_err = self._read_json()
        if read_err:
            security.security_log(BASE_DIR, "OVERSIZE_PAYLOAD",
                                  "来源 %s：%s" % (self.client_address[0], read_err))
            self._send(413, {"ok": False, "error": read_err})
            return

        if parsed.path == "/job":
            if not self._require_auth(body.get("token", "")):
                return
            job_type = body.get("type", "")
            if not job_type:
                self._send(400, {"ok": False, "error": "缺少 type 字段"})
                return
            # 安全检查：write_file 任务的文件名、内容
            params = body.get("params", {}) or {}
            if job_type == "write_file":
                target = str(params.get("path", ""))
                if security.is_blocked_filename(target):
                    security.security_log(BASE_DIR, "BLOCKED_JOB",
                                          "拒绝 write_file 禁扩名任务: " + target)
                    self._send(400, {"ok": False,
                                     "error": "拒绝：禁止的扩展名 %s（安全策略）"
                                     % os.path.splitext(target)[1]})
                    return
                high = security.is_high_risk(
                    security.scan_text(str(params.get("content", ""))))
                if high:
                    security.security_log(BASE_DIR, "BLOCKED_JOB",
                                          "拒绝 write_file 高危内容任务: %s，原因：%s"
                                          % (target, "; ".join(high)))
                    self._send(400, {"ok": False,
                                     "error": "拒绝：内容命中高危特征："
                                     + "; ".join(high)})
                    return
            jobs = load_jobs()
            job_id = uuid.uuid4().hex[:12]
            job = {
                "id": job_id,
                "type": job_type,
                "params": params,
                "status": "pending",
                "created_at": time.time(),
                "result": None,
            }
            jobs[job_id] = job
            save_jobs(jobs)
            self._send(200, {"ok": True, "id": job_id})
            return

        if parsed.path == "/result":
            if not self._require_auth(body.get("token", "")):
                return
            job_id = body.get("id", "")
            jobs = load_jobs()
            job = jobs.get(job_id)
            if not job:
                self._send(404, {"ok": False, "error": "任务不存在: " + job_id})
                return
            job["status"] = "done"
            job["result"] = body.get("result")
            job["finished_at"] = time.time()
            save_jobs(jobs)
            self._send(200, {"ok": True, "id": job_id})
            return

        self._send(404, {"ok": False, "error": "未知路径: " + parsed.path})


def main():
    print("muse-bridge 中转服务 v0.1")
    print("监听 %s:%d   jobs 文件: %s" % (HOST, PORT, JOBS_FILE))
    if TOKEN == "CHANGE_ME":
        print("警告: 正在用默认 token CHANGE_ME，请先设置环境变量 BRIDGE_TOKEN")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
