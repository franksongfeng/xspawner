# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
from xspawner.constants import * # NOQA
from xspawner.apps.spawner import * # NOQA
from xspawner.xspawner import * # NOQA
import tornado.queues
import os.path
import datetime
import json
import random
from typing import Optional, Dict, Any, List, Callable
from enum import Enum


class Severity(Enum):
    """日志级别枚举"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Logmon(Spawner):
    q = tornado.queues.Queue(1024)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    # display logs windows
    @ApiHandler.route("/")
    def _(self, headers: dict, data: dict):
        with open('{}/{}/static/index.html'.format(APP_DIR, self.getConfig().app), 'r', encoding='utf-8') as file:
            page = file.read()
        end_point = '{}/flow'.format(self.getAddr())
        return page.replace('ENDPOINT', end_point)

    # pop up logs from q
    @FlowHandler.route("/flow")
    def _flow(self, headers: dict, data: dict):
        logs = [self.q.get_nowait() for _ in range(self.q.qsize())]
        evt = {
            "event": "message",
            "data": json.dumps(logs, separators=(',', ':'), ensure_ascii=False)
        }
        return evt

    # push a log to q like the following request
    # ENDPOINT/queue?timestamp=2026-06-10T13:20:00&severity=info&system=unknown&content=Hello
    # severity: Log level, string type ('debug', 'info', 'warning', 'error', 'critical')
    # system: log source, string type of client/service name
    # timestamp: log time, string type of ISO 8601
    # content: log detail, string type
    @ApiHandler.route("/queue")
    def _queue(self, headers: dict, data: dict):
        self.iLog(f"receive {data}")
        if self.q.full():
            try:
                self.q.get_nowait()
            except tornado.queues.QueueEmpty:
                self.wLog("queue is empty")
        try:
            self.q.put_nowait(data)
        except tornado.queues.QueueFull:
            self.wLog("queue is full and cannot append")
        return True
