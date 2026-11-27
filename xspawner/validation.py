#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""服务标识、插件名与插件路径的校验工具。

这些值来自 HTTP 请求或上传文件名，最终会被拼进 systemd unit 名、SQLite 表名
（``t_<id>``）、URL 路径段（``/services/<id>/``）以及插件目录下的文件路径，
因此必须在入口处做白名单校验，不能只依赖调用方（UI）的输入限制。
"""

import os
import re

from xspawner.constants import PLUGIN_DIR


# 服务标识：会同时用作 systemd unit 名（<id>.service）、状态表名（t_<id>）、
# URL 路径段和数据库键。限制为 systemd 能接受的 ASCII 字符集，且必须以字母、
# 数字或下划线开头，从而排除空串、"."、".."、路径分隔符和空白。
SERVICE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")

# 插件名：xspawner.plugins 下的 Python 模块名（<name>/ 目录或 <name>.py）。
# 必须是合法标识符；点号、斜杠、连字符、空格都不允许。
PLUGIN_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# 不允许通过 clean/download 接口操作的内置插件。
RESERVED_PLUGIN_NAMES = frozenset({"spawner", "administrator"})


def is_valid_service_id(value) -> bool:
    """service id 是否满足白名单（systemd unit / 表名 / URL 安全）。"""
    return isinstance(value, str) and SERVICE_ID_RE.match(value) is not None


def is_valid_plugin_name(value) -> bool:
    """plugin 名是否为合法的 Python 模块名（xspawner.plugins.<name>）。"""
    return isinstance(value, str) and PLUGIN_NAME_RE.match(value) is not None


def is_valid_port(value) -> bool:
    """端口是否在 1..65535；允许整数、整数值浮点和数字字符串。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        port = value
    elif isinstance(value, float):
        if not value.is_integer():
            return False
        port = int(value)
    elif isinstance(value, str):
        if not value.isdigit():
            return False
        port = int(value)
    else:
        return False
    return 1 <= port <= 65535


def plugin_realpath(*parts) -> str:
    """把 ``PLUGIN_DIR`` 下的相对路径解析为真实路径，并强制其仍在插件根目录内。

    用于删除、下载、写入插件文件之前的兜底校验：即使上层校验被绕过，
    路径穿越（``..``、绝对路径）或指向目录外的软链接也无法逃出插件目录。

    参数按 ``os.path.join`` 拼接；返回 ``os.path.realpath`` 结果。
    越界时抛出 ``ValueError``。
    """
    if not parts:
        raise ValueError("empty plugin path")

    root = os.path.realpath(PLUGIN_DIR)
    target = os.path.realpath(os.path.join(PLUGIN_DIR, *parts))

    if target == root or target.startswith(root + os.sep):
        return target
    raise ValueError(f"path escapes plugin dir: {target}")
