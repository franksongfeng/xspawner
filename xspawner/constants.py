#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import os
import re
from collections import namedtuple

SOFTWARE_ID = "xspawner"
PLUGIN_PKG = f"{SOFTWARE_ID}.plugins"
PLUGIN_DIR = f"{SOFTWARE_ID}/plugins"
RES_DIR_TEMP = PLUGIN_DIR + "/{}/static"
PYTHON_MIME_TYPE = "text/x-python"
ZIP_MIME_TYPE = "application/zip"

# 运行数据目录：数据库、日志、备份等可变文件都放这里。
# 容器部署时用 XSPAWNER_DATA_DIR=/data 挂载持久化卷；
# 裸机部署保持默认当前目录，行为与旧版本一致。
DATA_DIR = os.path.abspath(os.environ.get("XSPAWNER_DATA_DIR", "."))

# 集群标识同时作为数据库文件名：<cluster>.db
_raw_cluster = os.environ.get("XSPAWNER_CLUSTER", "").strip()
CLUSTER = re.sub(r"[^A-Za-z0-9_.-]", "-", _raw_cluster).strip("-._") or SOFTWARE_ID
LOCAL_DB = os.path.join(DATA_DIR, f"{CLUSTER}.db")
LOCAL_DB_URL = "sqlite:///" + LOCAL_DB
LOG_FILE = os.path.join(DATA_DIR, f"{CLUSTER}.log")

Config = namedtuple('Config', ['id', 'plugin', 'host', 'port', 'access', 'parent', 'log', 'severity', 'ssl', 'certfile', 'keyfile', 'libs', 'additional'])