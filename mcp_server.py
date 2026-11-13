#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muse-bridge v0.3 MCP wrapper (mcp_server.py) — stdio JSON-RPC shim

这不是官方 `mcp` 包的服务器（那台 Linux 上没装 mcp，也不要全系统装），
而是一个按 JSON-RPC 2.0 over stdio 说话的瘦 shim：MCP 客户端
（Claude Desktop / Cursor 等）发 tools/list、tools/call，它就把工具调用
转成一次桥任务（走本机的 bridge_config.json → relay → Windows agent），
等结果回来再按 MCP 格式吐回去。

用法（在 Windows 的 C:\\muse-bridge 里跑，中转 + agent 要先起来）：
  python mcp_server.py
  # 测试一发：echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python mcp_server.py

Claude Desktop 配置（claude_desktop_config.json 里加一段，路径按你的改）：
  {
    "mcpServers": {
      "muse-bridge": {
        "command": "python",
        "args": ["C:\\\\muse-bridge\\\\mcp_server.py"],
        "cwd": "C:\\\\muse-bridge"
      }
    }
  }

想换成官方 mcp 包的正式服务器：pip install mcp，然后把下面的
handle_tools_call 里的逻辑搬进 @server.call_tool() 即可，工具名和参数不变。

工具（每个都先过桥的 token 认证；GUI 工具还要 gui_enabled.txt）：
  screenshot          {} / {"window": "记事本"}   截屏，返回本地路径 + 宽高
  click               {"x": 960, "y": 540}        点一下
  double_click        {"x": 960, "y": 540}        点两下
  type_text           {"text": "hello"}           打字（别输密码）
  press_key           {"key": "Enter"}            按一个键
  get_window          {}                          当前前台窗口
  list_dir            {"path": "C:\\muse-bridge\\work"}  列文件夹
  run_python          {"path": "hello.py"}        跑一个白名单内的 .py 脚本
  read_file_base64    {"path": "screenshot_xxx.jpg"}     下载二进制文件
"""

import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import send_job  # noqa: E402  (复用它发任务、等结果的逻辑)

TOOLS = {
    "screenshot": {
        "description": "Screenshot the Windows screen (JPG, max side 1280). "
                       "Optional window title substring to capture one window.",
        "inputSchema": {"type": "object",
                        "properties": {"window": {"type": "string"}},
                        "required": []},
        "job": "screenshot",
    },
    "click": {
        "description": "Click at screen pixels (x, y). Requires gui_enabled.txt.",
        "inputSchema": {"type": "object",
                        "properties": {"x": {"type": "integer"},
                                       "y": {"type": "integer"}},
                        "required": ["x", "y"]},
        "job": "click",
    },
    "double_click": {
        "description": "Double-click at screen pixels (x, y). Requires gui_enabled.txt.",
        "inputSchema": {"type": "object",
                        "properties": {"x": {"type": "integer"},
                                       "y": {"type": "integer"}},
                        "required": ["x", "y"]},
        "job": "double_click",
    },
    "type_text": {
        "description": "Type text (ASCII via keyboard, non-ASCII via clipboard paste). "
                       "Never use for passwords.",
        "inputSchema": {"type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"]},
        "job": "type_text",
    },
    "press_key": {
        "description": "Press one key, e.g. Enter, Esc, Tab, Ctrl+S, Alt+Tab.",
        "inputSchema": {"type": "object",
                        "properties": {"key": {"type": "string"}},
                        "required": ["key"]},
        "job": "press_key",
    },
    "get_window": {
        "description": "Get the foreground window title, position and size.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
        "job": "get_window",
    },
    "list_dir": {
        "description": "List files in an allowlisted folder.",
        "inputSchema": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
        "job": "list_dir",
    },
    "run_python": {
        "description": "Run an allowlisted .py script on the Windows PC.",
        "inputSchema": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
        "job": "run_python",
    },
    "read_file_base64": {
        "description": "Download an allowlisted binary file (e.g. a screenshot JPG) as base64.",
        "inputSchema": {"type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"]},
        "job": "read_file_base64",
    },
}


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(req_id, result):
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code, message):
    _send({"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": message}})


def handle_tools_call(name, arguments):
    """调一个工具：发桥任务、等结果、按 MCP 格式返回。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"content": [{"type": "text",
                             "text": "未知工具: %s，可用：%s" % (name, ", ".join(sorted(TOOLS)))}],
                "isError": True}
    for field in tool["inputSchema"].get("required", []):
        if field not in (arguments or {}):
            return {"content": [{"type": "text",
                                 "text": "缺少参数: %s" % field}],
                    "isError": True}
    try:
        cfg = send_job.load_config()
    except send_job.BridgeError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    relay = cfg["relay_url"].rstrip("/")
    token = cfg["token"]
    params = dict(arguments or {})
    # run_python / list_dir / read_file_base64 的 path 要解析成白名单内的绝对路径
    if tool["job"] in ("run_python", "list_dir", "read_file_base64") and params.get("path"):
        try:
            params["path"] = send_job.resolve_path(params["path"], cfg)
        except send_job.BridgeError as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    try:
        _job_id, _status, result = send_job.submit_and_wait(
            relay, token, tool["job"], params, timeout=120)
    except send_job.BridgeError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    # 截屏的 base64 太长，MCP 返回里只留元数据，图片文件在工作文件夹里
    if isinstance(result, dict):
        result = {k: v for k, v in result.items()
                  if k not in ("image_base64", "data_base64")}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}],
            "isError": not (isinstance(result, dict) and result.get("ok"))}


def handle_request(req):
    method = req.get("method", "")
    req_id = req.get("id")
    if method == "initialize":
        _result(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "muse-bridge", "version": "0.3"},
        })
    elif method in ("notifications/initialized", "notifications/cancelled"):
        return  # 通知，不用回
    elif method == "tools/list":
        _result(req_id, {"tools": [
            {"name": name,
             "description": spec["description"],
             "inputSchema": spec["inputSchema"]}
            for name, spec in TOOLS.items()]})
    elif method == "tools/call":
        params = req.get("params") or {}
        _result(req_id, handle_tools_call(params.get("name", ""),
                                          params.get("arguments") or {}))
    elif method == "ping":
        _result(req_id, {})
    else:
        _error(req_id, -32601, "不支持的方法: %s" % method)


def main():
    # MCP 协议走 stdout，日志只能走 stderr，不然会污染协议流
    print("muse-bridge MCP shim v0.3 (stdio JSON-RPC)，工具：%s"
          % ", ".join(sorted(TOOLS)), file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            _error(None, -32700, "JSON 解析失败: %s" % e)
            continue
        try:
            handle_request(req)
        except Exception as e:
            _error(req.get("id"), -32603, "内部错误: %s" % e)


if __name__ == "__main__":
    main()
