# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import tornado.gen
import tornado.concurrent
import tornado.web
import tornado.locks
import tornado.httpserver
import tornado.httputil

import os
import json
import importlib
import inspect
import traceback
import datetime
import types
import functools
import itertools
import ssl
from typing import List

from .utilities.log import DLine, ILine, WLine, ELine, CLine # NOQA


class ApiHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("POST", "GET", "OPTIONS")
    # path_map is path to handlers like dict {<post path> : <handle function>}
    #   url path begins with '/'
    path_map = {}

    def options(self):  
        self.set_status(204)
        self.finsih()

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.set_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")

    async def post(self):
        """ handle http/post request with data in body"""
        from xspawner import * # NOQA
        gServer = XSpawner.getServer()
        assert gServer
        q = gServer._req_queue
        future = tornado.concurrent.Future()
        condition = tornado.locks.Condition()
        path = self.request.path
        headers = self.request.headers
        body = self.request.body
        if path in self.path_map:
            elem = (future, condition, path, headers, body)
            await q.put(elem)
            cond_res = await condition.wait(timeout=datetime.timedelta(seconds=5))
            if cond_res and future._state == 'FINISHED':
                if isinstance(future.result(), tuple):
                    # download file
                    fdata, fname = future.result()
                    assert isinstance(fname, str) and \
                           isinstance(fdata, bytes)
                    self.set_header('Content-Type', 'application/octet-stream')
                    self.set_header('Content-Disposition', 'attachment; filename=%s' % fname)
                    self.write(fdata)
                else:
                    self.set_header('Content-Type', 'application/json')
                    if isinstance(future.result(), str):
                        self.write(future.result())
                    else:
                        self.write(json.dumps(future.result()))
            else:
                self.write('false')
        else:
            self.write('false')
        self.finish()

    async def get(self):
        """ handle http/get request with arguments in url"""
        from xspawner import * # NOQA
        gServer = XSpawner.getServer()
        assert gServer
        q = gServer._req_queue
        future = tornado.concurrent.Future()
        condition = tornado.locks.Condition()
        path = self.request.path
        headers = dict(self.request.headers.get_all())
        reqargs = self.request.arguments
        args = {arg: reqargs[arg][0].decode() for arg in reqargs}
        body = json.dumps(args).encode()
        if path in self.path_map:
            elem = (future, condition, path, headers, body)
            await q.put(elem)
            cond_res = await condition.wait(timeout=datetime.timedelta(seconds=5))
            if cond_res and future._state == 'FINISHED':
                if isinstance(future.result(), tuple):
                    # download file
                    fdata, fname = future.result()
                    assert isinstance(fname, str) and \
                           isinstance(fdata, bytes)
                    self.set_header('Content-Type', 'application/octet-stream')
                    self.set_header('Content-Disposition', 'attachment; filename=%s' % fname)
                    self.write(fdata)
                else:
                    self.set_header('Content-Type', 'text/html')
                    if isinstance(future.result(), str):
                        self.write(future.result())
                    else:
                        self.write(json.dumps(future.result()))
            else:
                self.write('false')
        else:
            self.write('false')
        self.finish()

    @classmethod
    def route(cls, path):
        def decorator(f):
            cls.path_map[path] = f
            return f
        return decorator