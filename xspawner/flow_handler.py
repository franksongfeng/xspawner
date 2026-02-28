# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import tornado.iostream
import tornado.gen
import tornado.web
from xspawner import * # NOQA

import os
import json
import inspect
import traceback
import datetime
import types
from typing import List

from .utilities.log import DLine, ILine, WLine, ELine, CLine # NOQA


class FlowHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("POST", "GET")
    path_map = {}

    def initialize(self):
        self.set_header('Content-Type', 'text/event-stream')
        self.set_header('Cache-Control', 'no-cache')
        self.set_header('Connection', 'keep-alive')

    def on_finish(self):
        WLine(f"on_finish BEG")
        path = self.request.path
        ILine(f"path = {path}")
        gServer = XSpawner.getServer()
        assert gServer
        # Ensure the client disconnects when the handler is finished
        if not self._finished:
            WLine(f"force to finish")
            self.finish()
        WLine(f"on_finish END")

    async def get(self):
        async def call_cbf(fun, *args, **kwargs):
            if inspect.iscoroutinefunction(fun):
                res = await fun(*args, **kwargs)
            else:
                res = fun(*args, **kwargs)
            return res
        # Send a message to the client every second
        path = self.request.path
        headers = dict(self.request.headers.get_all())
        ILine(f"request a stream {path}")
        # no Content-Type in self.request.headers
        # self.request.body should be empty
        gServer = XSpawner.getServer()
        assert gServer
        if path in self.path_map:
            f, varnames, timeout = self.path_map[path]
            assert isinstance(timeout, int) and (timeout > 0)
            reqargs = self.request.arguments
            args = {arg: reqargs[arg][0].decode() for arg in reqargs}
            try:
                while True:
                    evt = await call_cbf(f, gServer, headers, args)
                    if evt:
                        for k in evt:
                            text = "{}: {}\n".format(k, evt[k])
                            self.write(text)
                        self.write("\n")
                        await self.flush()
                        ILine(f"flush {evt}")
                    elif evt is False:
                        ILine(f"stream is closed from server {path}")
                        self.finish()
                        return
                    await tornado.gen.sleep(timeout)
            except tornado.iostream.StreamClosedError:
                WLine(f"stream closed error {path}")
                await tornado.gen.sleep(timeout)
            else:
                WLine(f"stream is closed on invalid condition {path}")
            self.finish()

    @classmethod
    def route(cls, path, timeout=300):
        def decorator(f):
            varnames = f.__code__.co_varnames[1:] if len(f.__code__.co_varnames) > 1 else ()
            cls.path_map[path] = (f, varnames, timeout)
            return f
        return decorator
