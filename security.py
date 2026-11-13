#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge 安全模块 v0.1（security.py）
标准库 only，Linux 和 Windows 两边都能 import。

这是一层基础纵深防御，不是杀毒软件，不能替代 Windows Defender
或任何正经杀毒软件。它能挡住的是：有人通过桥塞可执行文件、
改掉已批准的脚本、反复试口令这类“顺手牵羊”式的入侵。
零日漏洞、免杀木马、网络流量窃听，它都挡不住 —— README 里写明了。
"""

import hashlib
import json
import os
import re
import shutil
import time

# 绝不允许经桥写入 / 运行的扩展名
BLOCKED_EXTS = {
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".ps1", ".vbs", ".js",
    ".scr", ".msi", ".com", ".pif",
}

MAX_PAYLOAD_BYTES = 1 * 1024 * 1024        # 任务体上限 1MB
MAX_READ_BYTES = 5 * 1024 * 1024           # 读文件上限 5MB（与 agent 原有规则一致）
MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024      # 文本扫描最多读 2MB
MAX_FAILED_TOKENS = 5                       # 连续错口令 5 次后锁定
LOCKOUT_SECONDS = 10 * 60                  # 锁定 10 分钟

# 可疑文本特征（大小写不敏感，正则）。命中即标记，不直接定罪 ——
# 由 scan_file / scan_text 返回原因，再由调用方决定隔离或拒绝。
SUSPICIOUS_PATTERNS = [
    (r"powershell[^a-z0-9]{0,10}-enc", "powershell 编码执行 (-enc)"),
    (r"powershell[^a-z0-9]{0,10}-e\s+[A-Za-z0-9+/=]{20,}", "powershell 编码命令"),
    (r"invoke-expression", "Invoke-Expression 动态执行"),
    (r"iex\s*\(", "iex() 动态执行"),
    (r"rundll32", "rundll32 调用"),
    (r"certutil\s+.*-urlcache", "certutil 下载"),
    (r"bitsadmin\s+.*(/transfer|/create)", "bitsadmin 下载"),
    (r"mshta\s+", "mshta 执行"),
    (r"wscript|cscript", "wscript/cscript 执行"),
    (r"reg\s+add.*\\run", "写注册表自启动项"),
    (r"Invoke-WebRequest|Invoke-RestMethod", "PowerShell 联网下载"),
    (r"curl\s+.*\|\s*(sh|bash|powershell)", "curl 管道执行"),
    (r"wget\s+.*\|\s*(sh|bash|powershell)", "wget 管道执行"),
    (r"base64\s+-d.*\|\s*(sh|bash)", "base64 解码后执行"),
    (r"DownloadString|DownloadFile", "远程下载代码"),
    (r"from\s+base64\s+import|base64\.b64decode", "base64 解码（脚本内）"),
    (r"eval\s*\(|exec\s*\(", "eval/exec 动态执行"),
    (r"__import__\s*\(|subprocess\.Popen|os\.system\s*\(", "动态导入/系统命令执行"),
    (r"socket\.socket|urllib\.request\.urlopen|requests\.(get|post)", "脚本内联网"),
]
_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), label)
                      for p, label in SUSPICIOUS_PATTERNS]

# 注意：上面几条在正经脚本里也可能出现（比如脚本自己要用 subprocess 跑
# 中望、要用 urllib 拉数据）。所以 scan_file 的结果是“标记 + 原因”，
# 只有“编码执行 / 远程下载后执行 / 写自启动”这类高危组合才直接拒绝；
# 一般标记由调用方记日志并交用户判断。真正硬拒绝的是：禁扩名、
# 哈希变了的脚本、超大载荷、口令锁定。


HIGH_RISK_KEYWORDS = ("编码执行", "编码命令", "管道执行", "远程下载", "自启动",
                      "rundll32", "mshta", "Invoke-Expression", "iex()")


def is_high_risk(reasons):
    """从 scan_text 返回的原因里挑出高危项（命中即应拒绝/隔离）。"""
    return [r for r in reasons if any(k in r for k in HIGH_RISK_KEYWORDS)]


def security_log(base_dir, event, detail=""):
    """写 security.log（与 audit.log 分开）。永不抛错。"""
    line = "%s | SECURITY | %s | %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), event, str(detail)[:500])
    try:
        with open(os.path.join(base_dir, "security.log"), "a",
                  encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def is_blocked_filename(name):
    """文件名（或路径）的扩展名是否在禁单上。"""
    ext = os.path.splitext(str(name))[1].lower()
    return ext in BLOCKED_EXTS


def scan_text(content):
    """扫一段文本，返回命中的可疑原因列表（空 = 干净）。"""
    if not isinstance(content, str):
        try:
            content = content.decode("utf-8", errors="replace")
        except Exception:
            return []
    reasons = []
    for pattern, label in _COMPILED_PATTERNS:
        if pattern.search(content):
            reasons.append(label)
    return reasons


def scan_file(path):
    """扫一个磁盘文件。返回 (ok, reasons)：ok=False 表示应拒绝/隔离。

    拒绝条件：禁扩名、文件不存在、读不到、超大（>5MB 不扫直接拒）。
    可疑文本只返回原因，不直接判 False —— 调用方决定是隔离还是放行，
    例外：命中“编码执行 / 下载后执行 / 写自启动”这类高危特征时直接拒。
    """
    if not os.path.isfile(path):
        return False, ["文件不存在: " + str(path)]
    if is_blocked_filename(path):
        return False, ["禁止的扩展名: " + os.path.splitext(path)[1]]
    try:
        size = os.path.getsize(path)
    except Exception as e:
        return False, ["无法读取文件大小: %s" % e]
    if size > MAX_READ_BYTES:
        return False, ["文件超过 5MB，拒绝处理: %s" % path]
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_TEXT_SCAN_BYTES)
    except Exception as e:
        return False, ["无法读取文件: %s" % e]
    # 二进制文件（不可解码为文本的）不在文本扫描范围内，放行文本检查
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return True, []
    reasons = scan_text(text)
    high_risk = is_high_risk(reasons)
    if high_risk:
        return False, high_risk
    return True, reasons


# ---------------- 脚本哈希白名单 ----------------

def _load_hashes(hashes_file):
    try:
        with open(hashes_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_hashes(hashes_file, hashes):
    tmp = hashes_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hashes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, hashes_file)


def check_script_hash(path, hashes_file):
    """脚本哈希检查。返回 (ok, status, reasons)。

    status: "first"（首次运行，记录哈希并放行）、"match"（与记录一致）、
            "changed"（哈希变了，拒绝运行，需用户确认后批准）。
    """
    digest = sha256_file(path)
    hashes = _load_hashes(hashes_file)
    key = os.path.abspath(path)
    recorded = hashes.get(key)
    if recorded is None:
        hashes[key] = {"sha256": digest, "first_seen": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save_hashes(hashes_file, hashes)
        return True, "first", ["首次运行，已记录哈希: " + digest[:16] + "..."]
    if recorded.get("sha256") == digest:
        return True, "match", []
    return False, "changed", [
        "脚本哈希变了！记录 %s... 现在 %s...，拒绝运行。请检查脚本是否被改过，"
        "确认无误后用 approve_script_hash 批准。" % (
            str(recorded.get("sha256", ""))[:16], digest[:16])]


def approve_script_hash(path, hashes_file):
    """用户确认脚本无误后，更新记录的哈希（批准新版本）。"""
    digest = sha256_file(path)
    hashes = _load_hashes(hashes_file)
    hashes[os.path.abspath(path)] = {
        "sha256": digest,
        "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        "approved": True,
    }
    _save_hashes(hashes_file, hashes)
    return digest


# ---------------- 隔离 ----------------

def quarantine(path, quarantine_dir):
    """把可疑文件移到 quarantine/ 文件夹，永不执行。返回目标路径。"""
    os.makedirs(quarantine_dir, exist_ok=True)
    base = os.path.basename(path)
    dest = os.path.join(quarantine_dir, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(
            quarantine_dir, "%s_%s%s" % (stem, time.strftime("%Y%m%d_%H%M%S"), ext))
    shutil.move(path, dest)
    return dest


# ---------------- 口令锁定 ----------------

class TokenLockout:
    """连续错口令计数，5 次后锁定 10 分钟。状态存在磁盘文件里，重启不丢。"""

    def __init__(self, state_file):
        self.state_file = state_file

    def _load(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data):
        try:
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.state_file)
        except Exception:
            pass

    def is_locked(self):
        state = self._load()
        locked_until = float(state.get("locked_until", 0))
        if locked_until and time.time() < locked_until:
            return True, int(locked_until - time.time())
        return False, 0

    def record_failure(self):
        state = self._load()
        fails = int(state.get("fails", 0)) + 1
        state["fails"] = fails
        if fails >= MAX_FAILED_TOKENS:
            state["locked_until"] = time.time() + LOCKOUT_SECONDS
            state["fails"] = 0
            self._save(state)
            return True  # 刚触发锁定
        self._save(state)
        return False

    def record_success(self):
        state = self._load()
        if state.get("fails") or state.get("locked_until"):
            self._save({"fails": 0, "locked_until": 0})
