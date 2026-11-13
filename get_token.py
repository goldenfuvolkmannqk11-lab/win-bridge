#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只打印 bridge_config.json 里的 token，供 start_bridge.bat 读取。

只往 stdout 打印 token 本身，不打印别的，这样 bat 才能用 for /f 接住。
token 找不到或文件读不到时往 stderr 报错并退出 1。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    with open(os.path.join(HERE, "bridge_config.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    print(cfg.get("token", ""))
except Exception as e:
    print("读 bridge_config.json 失败：%s" % e, file=sys.stderr)
    sys.exit(1)
