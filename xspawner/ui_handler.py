# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import pywebio.platform.tornado

import os
import json
import traceback
import functools

from .utilities.log import DLine, ILine, WLine, ELine, CLine # NOQA


class UiHandler:
    path_map = {}

    def __new__(cls, path, srv):
        f = cls.path_map[path]
        WIOHandler = pywebio.platform.tornado.webio_handler(
            functools.partial(f, srv),
            reconnect_timeout=3,
            check_origin=False
        )
        class UIHandler(WIOHandler):
            def set_default_headers(self):
                self.set_header("Access-Control-Allow-Origin", "*")
                self.set_header("Access-Control-Allow-Headers", "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization")
                self.set_header("Access-Control-Allow-Methods", "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS")
                self.set_header("Access-Control-Expose-Headers", "Content-Length,Content-Range")
            # optional
            def options(self, *args, **kwargs):
                self.set_header("Access-Control-Allow-Origin", "*")
                self.set_header("Access-Control-Allow-Headers", "DNT,User-Agent,X-Requested-With,If-Modified-Since,Cache-Control,Content-Type,Range,Authorization")
                self.set_header("Access-Control-Allow-Methods", "GET,HEAD,POST,PUT,PATCH,DELETE,OPTIONS")
                self.set_header("Access-Control-Max-Age", "1728000")
                self.set_header("Content-Type", "text/plain; charset=utf-8")
                self.set_status(204)
                self.finish()
        return UIHandler

    @classmethod
    def route(cls, path):
        def decorator(f):
            cls.path_map[path] = f
            return f
        return decorator