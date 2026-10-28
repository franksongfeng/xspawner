#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from collections import namedtuple

SUITE_ID = "xspawner"
# WORKING_DIR = f"/opt/{SUITE_ID}"
SOFTWARE_ID = "xspawner"
PLUGIN_PKG = f"{SOFTWARE_ID}.plugins"
PLUGIN_DIR = f"{SOFTWARE_ID}/plugins"
RES_DIR_TEMP = PLUGIN_DIR + "/{}/static"
PYTHON_MIME_TYPE = "text/x-python"
ZIP_MIME_TYPE = "application/zip"
LOCAL_DB = f"{SOFTWARE_ID}.db"
LOG_FILE = f"{SOFTWARE_ID}.log"

Config = namedtuple('Config', ['id', 'plugin', 'host', 'port', 'access', 'parent', 'log', 'severity', 'ssl', 'certfile', 'keyfile', 'libs', 'additional'])