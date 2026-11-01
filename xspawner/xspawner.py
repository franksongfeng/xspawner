# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

##############################################################################
# Libraries
##############################################################################

# Standard Libraries
import time
import os
import sys
import json
import psutil
import importlib
import inspect
import traceback
import datetime
import types
import functools
import itertools
import zipfile
import base64
import uuid
import ssl
import unittest
import shutil
from urllib.parse import urlparse
from typing import Optional, List, Dict
from collections import UserDict
import ipaddress
import asyncio


# Third Libraries
import tornado.ioloop
import tornado.iostream
import tornado.gen
import tornado.queues
import tornado.concurrent
import tornado.web
import tornado.escape
import tornado.locks
import tornado.httpserver
import tornado.httpclient
import tornado.httputil
import tornado.tcpclient
from tortoise.exceptions import DoesNotExist
import pywebio.platform.tornado

# Self Libraries
from xspawner.log import Log # NOQA
from xspawner.constants import * # NOQA
from xspawner.orm import open_database, drop_database, config_model_to_tuple, ConfigModel, StateModel #NOQA
from xspawner.service import open_service, close_service, get_service_status # NOQA
from xspawner.proxy import _prefix, caddy_is_running, caddy_find_server_by_site_address, caddy_add_route, caddy_del_route  # NOQA
from xspawner.misc import wait_port_sync, getSSLContext, zip_folder, make_multipart_request, make_multipart_form, make_docs, parse_reply, get_file_type, is_module_available, is_port_used, getWorkDir # NOQA


##############################################################################
# Classes
##############################################################################

class PingPongHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET",)
    # ping/pong test
    def get(self):
        self.write('pong')
        self.finish()

class HomePageHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET",)
    def get(self):
        self.set_header("Content-Type", "text/html")
        self.render("static/index.html")


class ResourceHandler(tornado.web.StaticFileHandler):
    def set_default_headers(self):
        self.set_header('Cache-Control', 'max-age=0, no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')


class ApiHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("POST", "GET", "OPTIONS")
    # path_map is path to handlers like dict {<post path> : <handle function>}
    #   url path begins with '/'
    path_map = {}

    def options(self):
        self.set_status(204)
        self.finish()

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.set_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")

    async def _handle(self, path, headers, body, content_type):
        """POST/GET 共用的转发+等待+回写逻辑。"""
        gServer = XSpawner.getServer()
        assert gServer
        q = gServer._req_queue

        if path not in self.path_map:
            self.write('false')
            self.finish()
            return

        future = tornado.concurrent.Future()
        condition = tornado.locks.Condition()
        await q.put((future, condition, path, headers, body))

        cond_res = await condition.wait(timeout=datetime.timedelta(seconds=60))
        if cond_res and future.done():
            result = future.result()
            if isinstance(result, tuple):
                # download file
                fdata, fname = result
                assert isinstance(fname, str) and isinstance(fdata, bytes)
                self.set_header('Content-Type', 'application/octet-stream')
                self.set_header('Content-Disposition',
                                'attachment; filename=%s' % fname)
                self.write(fdata)
            else:
                self.set_header('Content-Type', content_type)
                self.write(result if isinstance(result, str)
                           else json.dumps(result))
        else:
            self.write('false')
        self.finish()

    async def post(self):
        """ handle http/post request with data in body"""
        await self._handle(
            path=self.request.path,
            headers=self.request.headers,
            body=self.request.body,
            content_type='application/json',
        )

    async def get(self):
        """ handle http/get request with arguments in url"""
        reqargs = self.request.arguments
        args = {arg: reqargs[arg][0].decode() for arg in reqargs}
        await self._handle(
            path=self.request.path,
            headers=dict(self.request.headers.get_all()),
            body=json.dumps(args).encode(),
            content_type='text/html; charset=utf-8',
        )

    @classmethod
    def route(cls, path):
        def decorator(f):
            cls.path_map[path] = f
            return f
        return decorator


class UiHandler:
    path_map = {}

    def __new__(cls, path, srv):
        f = cls.path_map[path]
        WIOHandler = pywebio.platform.tornado.webio_handler(
            functools.partial(f, srv),
            reconnect_timeout=3,
            allowed_origins=['*']
        )
        return WIOHandler

    @classmethod
    def route(cls, path):
        def decorator(f):
            cls.path_map[path] = f
            return f
        return decorator


# ProxyHandler的使用方法：在 handlers 中添加 (r"/services/([^/]+)/?(.*)", ProxyHandler)
# 注意： ProxyHandler 只能处理普通 HTTP 请求, 不适合 SSE /WebSocket 类长连接端点, 
# 此类长连接页面要么直接访问，要么让真正的反向代理（Caddy/Nginx）做透明转发。

class ProxyHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

    async def _proxy(self, method):
        gServer = XSpawner.getServer()
        gServer.iLog(f"ProxyHandler._proxy BEG {method} {self.path_args} use_caddy={gServer.use_caddy()}")
        srv_id = self.path_args[0]
        rest = self.path_args[1] or ""
        child = await gServer.getConfig(srv_id)
        if not child:
            self.set_status(404)
            self.write("Not Found Config of {srv_id}")
            return
        scheme = self.request.protocol
        upstream = "{}://127.0.0.1:{}/{}".format(scheme, child.port, rest)
        if self.request.query:
            upstream += "?" + self.request.query
        headers = {k: v for k, v in self.request.headers.items()
                   if k.lower() not in ("host", "content-length")}
        headers["X-Forwarded-For"] = self.request.remote_ip
        headers["X-Forwarded-Proto"] = self.request.protocol
        headers["X-Forwarded-Host"] = self.request.host
        try:
            resp = await tornado.httpclient.AsyncHTTPClient().fetch(
                upstream, method=method, headers=headers,
                body=self.request.body or None,
                follow_redirects=False, raise_error=False,
                validate_cert=False, request_timeout=60)
        except Exception as e:
            gServer.eLog("proxy {} {} failed: {}".format(method, srv_id, e))
            self.set_status(502)
            self.write("Bad Gateway")
            return
        self.set_status(resp.code)
        for k, v in resp.headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.set_header(k, v)
        if resp.body:
            self.write(resp.body)
        self.finish()
        gServer.iLog("ProxyHandler._proxy END")

    async def get(self, srv_id, rest):
        await self._proxy("GET")
    async def post(self, srv_id, rest):
        await self._proxy("POST")
    async def put(self, srv_id, rest):
        await self._proxy("PUT")
    async def delete(self, srv_id, rest):
        await self._proxy("DELETE")
    async def patch(self, srv_id, rest):
        await self._proxy("PATCH")
    async def head(self, srv_id, rest):
        await self._proxy("HEAD")
    async def options(self, srv_id, rest):
        await self._proxy("OPTIONS")


class Spawnable(object):

    def __init__(self, config: Config, **others):
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def getPid(self):
        raise NotImplementedError

    def getAddr(self):
        raise NotImplementedError

    def getLocalAddr(self, port):
        raise NotImplementedError

    def use_caddy(self):
        raise NotImplementedError

    async def init_db(self):
        raise NotImplementedError

    async def add_route(self):
        raise NotImplementedError

    async def del_route(self):
        raise NotImplementedError

    async def getValues(self):
        raise NotImplementedError

    async def getValue(self, key):
        raise NotImplementedError

    async def setValue(self, key, value):
        raise NotImplementedError

    async def delValue(self, key):
        raise NotImplementedError

    async def getConfigs(self):
        raise NotImplementedError

    async def getRootConfig(self):
        raise NotImplementedError

    async def getConfig(self, id):
        raise NotImplementedError

    async def delConfig(self, id):
        raise NotImplementedError

    async def addConfig(self, config):
        raise NotImplementedError

    async def getChildren(self):
        raise NotImplementedError


class XSpawner(Spawnable):
    _instance = None
    _logger = None
    _loop = None
    _config: Config = None

    # host is external access address
    # port is internal listening port
    # _config is immutable boot parameters for _instance
    # _ioloop is the only main event loop
    # _server is http server instance
    # _logger is logging instance
    # _instance is xspawner singleton service

    def __init__(self, config, **kwargs):
        print("__init__ BEG {} {}".format(config, kwargs))
        self._config = config

        self._fatal = False

        # core queue
        self._ioloop = tornado.ioloop.IOLoop.current()

        # create request queue
        self._req_queue = tornado.queues.Queue(256)


        # start logger
        self._logger = Log(
            config.id,
            "file",
            self.getLogFile(),
            config.severity
        )

        handlers = []
        # user handlers are prior
        for path in ApiHandler.path_map:
            handlers.append((path, ApiHandler))
        for path in UiHandler.path_map:
            handlers.append((path, UiHandler(path, self)))
        # fixed handlers are at the bottom
        handlers.extend([
            (r"/", HomePageHandler),
            (r"/static/(.*)", ResourceHandler, {"path": RES_DIR_TEMP.format(config.plugin)}),
            (r"/ping", PingPongHandler)
        ])
        # start http server
        app = tornado.web.Application(
            handlers=handlers,
            default_host="127.0.0.1"
        )
        if config.ssl and config.certfile and config.keyfile:
            self._server = tornado.httpserver.HTTPServer(
                app,
                ssl_options=getSSLContext(config.certfile, config.keyfile)
            )
            self.iLog("httpserver is with specific ssl certification")
        else:
            self._server = tornado.httpserver.HTTPServer(app)
            self.iLog("httpserver is without specific ssl certification")
        print("__init__ END")


    def dLog(self, *content):
        self._logger.debug("; ".join(str(x) for x in content))

    def iLog(self, *content):
        self._logger.info("; ".join(str(x) for x in content))

    def wLog(self, *content):
        self._logger.warning("; ".join(str(x) for x in content))

    def eLog(self, *content):
        self._logger.error("; ".join(str(x) for x in content))

    def cLog(self, *content):
        self._logger.critical("; ".join(str(x) for x in content))


    # consumer for queue
    async def loop(self):
        self.iLog("loop BEG")
        def parse_multipart_boundary(ct):
            assert isinstance(ct, str)
            [ct1, ct2] = ct.split("; ")
            [ct2a, ct2b] = ct2.split("=")
            return ct2b

        async def call_cbf(fun, *args, **kwargs):
            if inspect.iscoroutinefunction(fun):
                res = await fun(*args, **kwargs)
            else:
                res = fun(*args, **kwargs)
            return res

        def make_cbf_params(fun, ct, body):
            # fun.__code__.co_argcount = 1(self) + N({}|args|fdata, fname, farg)
            if fun.__code__.co_argcount == 1: # for UiHandler
                self.iLog("{} without params".format(fun.__code__.co_name))
                return ()
            elif fun.__code__.co_argcount == 3: # for ApiHandler I (normal)
                # transport JSON
                jdata = body.decode()
                self.iLog("{} with params {}".format(fun.__code__.co_name, jdata))
                return (json.loads(jdata),)
            elif fun.__code__.co_argcount == 5: # for ApiHandler II (upload)
                # transport File
                boundary = parse_multipart_boundary(ct)
                args, docs = dict(), dict()
                tornado.httputil.parse_multipart_form_data(boundary.encode(), body, args, docs)
                # if filename is not empty string
                #   args is dict like {"v1": [b"xyz"]}
                #   docs is dict like {"file": [{"filename": "xFEs3r8w.html", "body": b"....", "content_type": "text/html"}]
                # else
                #   args is dict like {"file": [b"...."], "v1": [b"xyz"]}
                #   docs is empty dict {}
                if docs:
                    lv = list(docs.values())
                    fdata = lv[0][0]["body"]
                    fname = lv[0][0]["filename"]
                else:
                    fdata = args.pop("file")[0]
                    fname = ""
                fargs = {k: args[k][0].decode() for k in args}
                self.iLog("{} with params [{}] {} {}".format(fun.__code__.co_name, len(fdata), fname, fargs))
                return (fdata, fname, fargs)
            else:
                self.eLog('{}: invalid argument count {}'.format(fun.__code__.co_name, fun.__code__.co_argcount))


        async for (future, condition, path, headers, body) in self._req_queue:
            try:
                fun = ApiHandler.path_map[path]
                ct = headers["Content-Type"] if "Content-Type" in headers else None
                self.dLog("make_cbf_params {} {} {}".format(fun, ct, body))
                params = make_cbf_params(fun, ct, body)
                if isinstance(params, tuple):
                    res = await call_cbf(fun, self, headers, *params) if params else await call_cbf(fun, self)
                    if any([isinstance(res, t) for t in (str, bool, dict , int, float, list, tuple)]):
                        if not future.done():
                            future.set_result(res)
                            condition.notify()
                    else:
                        # invalid res type
                        raise Exception("invalid response type {} {}".format(fun.__code__.co_name, res))
                else:
                    # invalid args
                    raise Exception("invalid arguments type {} {}".format(fun.__code__.co_name, params))
            except Exception as e:
                self.eLog(traceback.format_exc())
                if not future.done():
                    self.eLog(repr(e))
                    future.set_result(False)
                    condition.notify()
            finally:
                self._req_queue.task_done()
        self.cLog("loop is going to stop...")
        self.stop()
        self.iLog("loop END")


    def start(self):
        self.iLog("start BEG")
        self._fatal = False
        self._ioloop.add_callback(self.loop)
        self._ioloop.add_callback(self.init_db)
        self._ioloop.add_callback(self.add_route)
        try:
            self._server.listen(self._config.port, address=self._config.access)
            self.iLog("listening to port {}...".format(self._config.port))
        except Exception:
            self.eLog(traceback.format_exc())
            sys.exit(1)
        try:
            self._ioloop.start()
            self.iLog("ioloop stopped")
        except Exception:
            self.eLog(traceback.format_exc())
            self._fatal = True
        finally:
            try:
                self._ioloop.close()
            except Exception:
                self.eLog(traceback.format_exc())
        if self._fatal:
            self.cLog("start END fatal, sys.exit(1)")
            sys.exit(1)
        self.iLog("start END")

    def stop(self):
        self.iLog("stop BEG")
        self.cLog("This instance {}:{} is stopping ...".format(self.__class__, self._config.port))

        async def _shutdown():
            try:
                await self.del_route()
            finally:
                self._server.stop()
                self._ioloop.stop()

        self._ioloop.add_callback(_shutdown)
        self.iLog("stop END")

    def getAddr(self):
        self.iLog("getAddr BEG")
        scheme = "https" if self._config.ssl else "http"
        host_addr = "{}://{}".format(scheme, self._config.host)
        if self.use_caddy():
            # addr like https://HOST/services/ID
            addr = "{}{}".format(host_addr, _prefix(self._config.id))
        else:
            addr = "{}:{}".format(host_addr, self._config.port)
        self.iLog(f"getAddr END {addr}")
        return addr

    def getLocalAddr(self, port=None):
        self.iLog(f"getLocalAddr BEG {port}")
        addr = "{}://127.0.0.1:{}".format(
            "https" if self._config.ssl and not self.use_caddy() else "http",
            port if port is not None else self._config.port)
        self.iLog(f"getLocalAddr END")
        return addr

    def getPid(self):
        return os.getpid()

    async def init_db(self):
        self.iLog("init_db BEG")
        try:
            connection_str = f"sqlite://{LOCAL_DB}"
            extra_model_mod = "{}.{}".format(PLUGIN_PKG, self._config.plugin)
            await open_database(connection_str, extra_model_mod)
            if not await self.getConfig(self._config.id):
                # await tornado.gen.sleep(0.5)
                await self.addConfig(self._config)
                self.iLog(f"new a model {self._config}")
            self.iLog("init_db END")
        except Exception as e:
            self.eLog(f"init_db raise {e}")
            self.cLog("init_db failed, no db => fatal")
            self._fatal = True
            self._server.stop()
            self._ioloop.stop()

    def use_caddy(self):
        self.iLog("use_caddy BEG")
        res = self._config.ssl and (not self._config.certfile) and (not self._config.keyfile)
        self.iLog(f"use_caddy END {res}")
        return res

    async def add_route(self):
        self.iLog("add_route BEG")
        if self.use_caddy():
            if await caddy_is_running():
                ok = await caddy_add_route(self._config.host, self._config.id, self._config.port)
                self.iLog(f"caddy add route {self._config.id}:{self._config.port} {ok}")
            else:
                raise Exception("Caddy is out of work!")
        self.iLog("add_route END")

    async def del_route(self):
        self.iLog("del_route BEG")
        if self.use_caddy():
            if await caddy_is_running():
                ok = await caddy_del_route(self._config.host, self._config.id)
                self.iLog(f"caddy del route {self._config.id} {ok}")
            else:
                raise Exception("Caddy is out of work!")
        self.iLog("del_route END")

    async def getValues(self) -> dict:
        self.iLog(f"getValues BEG config={self._config.id}")
        try:
            models = await StateModel.filter(config_id=self._config.id).all()
            rt = {m.key: m.value for m in models}
            self.iLog(f"getValues END {len(rt)}")
            return rt
        except Exception as e:
            self.eLog(f"getValues EXP: {e}")
            return {}

    async def getValue(self, key: str):
        self.iLog(f"getValue BEG key={key} config={self._config.id}")
        if not key:
            self.wLog("getValue: empty key")
            return None
        try:
            model = await StateModel.get_or_none(
                key=key,
                config_id=self._config.id
            )
            if model is None:
                self.iLog(f"getValue END {key} no-hit")
                return None
            self.iLog(f"getValue END {key} type={type(model.value).__name__}")
            return model.value
        except Exception as e:
            self.eLog(f"getValue EXP {key}: {e}")
            return None


    async def setValue(self, key: str, value) -> bool:
        self.iLog(f"setValue BEG key={key} config={self._config.id} type={type(value).__name__}")
        if not key:
            self.wLog("setValue: empty key")
            return False
        try:
            model = await StateModel.get_or_none(
                key=key,
                config_id=self._config.id
            )
            if model:
                model.value = value
                await model.save()
                self.iLog(f"setValue END {key} updated")
            else:
                await StateModel.create(
                    key=key,
                    config_id=self._config.id,
                    value=value
                )
                self.iLog(f"setValue END {key} created")
            return True
        except Exception as e:
            self.eLog(f"setValue EXP {key}: {e}")
            return False

    async def delValue(self, key: str) -> bool:
        self.iLog(f"delValue BEG key={key} config={self._config.id}")
        if not key:
            self.wLog("delValue: empty key")
            return False
        try:
            deleted = await StateModel.filter(
                key=key,
                config_id=self._config.id,
            ).delete()
            self.iLog(f"delValue END {key} deleted={deleted}")
            return True
        except Exception as e:
            self.eLog(f"delValue EXP {key}: {e}")
            return False

    async def getConfigs(self) -> Optional[List[Config]]:
        self.iLog(f"getConfigs BEG")
        try:
            models = await ConfigModel.all()
            ones = [config_model_to_tuple(m) for m in models]
            self.iLog(f"getConfigs END {len(ones)}")
            return ones
        except Exception as e:
            self.eLog(f'getConfigs EXP {e}')
            return None


    async def getRootConfig(self) -> Optional[Config]:
        """
        沿 parent 链向上查找，返回服务集群根节点的 Config。

        - 当前实例本身就是根（parent 为空）时，返回 self._config
        - 链上某个 parent 在 DB 中不存在时，返回 None（并记 error 日志）
        - 出现环（A→B→A）时，返回 None（并记 error 日志）
        """
        self.iLog("getRootConfig BEG")
        cur = self._config
        seen = {cur.id}
        while cur.parent:
            nxt = await self.getConfig(cur.parent)
            if not nxt:
                self.eLog("getRootConfig EXP: parent '{}' of '{}' not found".format(cur.parent, cur.id))
                return None
            if nxt.id in seen:
                self.eLog("getRootConfig EXP: cycle detected at '{}'".format(nxt.id))
                return None
            seen.add(nxt.id)
            cur = nxt
        self.iLog(f"getRootConfig END {cur}")
        return cur


    async def getConfig(self, id: str) -> Optional[Config]:
        self.iLog(f"getConfig BEG {id}")
        try:
            model = await ConfigModel.get(id=id)
        except DoesNotExist:
            self.iLog(f"getConfig END No")
            return None
        except Exception as e:
            self.eLog(f'getConfig EXP {e}')
            return None
        one = config_model_to_tuple(model)
        self.iLog(f"getConfig END {one}")
        return one

    async def delConfig(self, id: str) -> bool:
        self.iLog(f"delConfig BEG {id}")
        try:
            model = await ConfigModel.get(id=id)
            await model.delete()
            self.iLog("delConfig END")
            return True
        except DoesNotExist:
            self.iLog(f"delConfig END No")
        except Exception as e:
            self.eLog(f'delConfig EXP {e}')
        return False

    async def addConfig(self, config: Config) -> bool:
        self.iLog(f"addConfig BEG {config}")
        data = config._asdict()
        if data.get('parent'):
            parent_id = data['parent']
            try:
                parent_obj = await ConfigModel.get(id=parent_id)
                # set parent with object or set parent_id with value
                data['parent'] = parent_obj
            except DoesNotExist:
                self.eLog(f"Parent '{parent_id}' not found, setting parent to None")
                data['parent'] = None
        else:
            data['parent'] = None

        if data.get('certfile') is None:
            data['certfile'] = ""
        if data.get('keyfile') is None:
            data['keyfile'] = ""
        if data.get('libs') is None:
            data['libs'] = ""
        if data.get('additional') is None:
            data['additional'] = ""

        try:
            model = await ConfigModel.create(**data)
            self.iLog(f"addConfig END {model}")
            return True
        except Exception as e:
            self.eLog(f'addConfig EXP {e}')
            return False

    async def getChildren(self) -> List[str]:
        self.iLog(f"getChildren BEG")
        try:
            models = await ConfigModel.filter(parent_id=self._config.id).all()
            self.iLog(f"Type: {type(models)} Len: {len(models)} Models: {models}") # Models: [ConfigModel]
            ids = [m.id for m in models]
            self.iLog(f"getChildren END {ids}")
            return ids
        except Exception as e:
            self.eLog(f'getChildren EXP: {e}')
            return []


    # 所有子类共享单例
    @classmethod
    def getServer(cls,**kwargs):
        if XSpawner._instance is None:
            XSpawner._instance = cls(
                **kwargs
            )
            print("{}::getServer XSpawner's singleton is created".format(cls.__name__))
        return XSpawner._instance

    @classmethod
    def isChildClass(cls, kls):
        return issubclass(kls, cls) and kls is not cls

    async def testServer(self, port, host="127.0.0.1", use_ssl=False):
        self.iLog("testServer BEG {} {} {}".format(port, host, use_ssl))
        tcp_client = tornado.tcpclient.TCPClient()
        try:
            stream = await tcp_client.connect(host, port, ssl_options=ssl.create_default_context() if use_ssl else None)
            stream.close()  # connect succeed and clost it
            self.iLog("testServer END true")
            return True
        except Exception as e:
            self.eLog(f'WARN: {host}:{port} is not connected: {e}')
            self.wLog("testServer END false")
            return False    # failed and stop connect try

    # jdata is dict or bool/int/list or None
    async def postJson(self, url, jdata):
        self.iLog("postJson BEG {} {}".format(url, jdata))
        headers = {
            "Content-Type": "application/json"
        }
        body = json.dumps(jdata) 
        rt = await postAsync(url, headers, body)
        self.iLog("postJson END {}".format(rt))
        return rt

    # fdata is bytes
    # fname is str
    # fargs is dict of {str:str}
    async def postForm(self, url, fdata, fname, fargs={}):
        self.iLog("postForm BEG {} {} {} {}".format(url, fdata, fname, fargs))
        args = {k: [str(fargs[k]).encode()] for k in fargs}
        docs = {
            "file": [
                {
                    "filename": fname,
                    "body": fdata,
                    "content_type": get_file_type(fname)
                }
            ]
        } if fdata or fname else {}
        headers, body = make_multipart_request(args, docs)
        rt = await postAsync(url, headers, body)
        self.iLog("postForm END {rt}".format(rt))
        return rt

    async def waitForService(self, port, host="127.0.0.1", timeout=30, interval=0.5):
        self.iLog(f"waitForService BEG host={host} port={port} timeout={timeout}")
        deadline = time.time() + timeout
        tcp_client = tornado.tcpclient.TCPClient()
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                stream = await tcp_client.connect(host, port)
                stream.close()
                self.iLog(f"waitForService END ready {host}:{port} (attempt {attempt})")
                return True
            except Exception as e:
                self.iLog(f"waitForService attempt {attempt} failed: {type(e).__name__}: {e}")
            await tornado.gen.sleep(interval)
        self.eLog(f"waitForService END timeout {host}:{port}")
        return False

    def getLogFile(self):
        return LOG_FILE

    def getPidTime(self, pid=None):
        if not pid:
            pid = self.getPid()
        try:
            process = psutil.Process(pid)
            start_timestamp = process.create_time()
            formatted_time = datetime.datetime.fromtimestamp(start_timestamp).strftime("%Y-%m-%d %H:%M:%S")
            return formatted_time
        except Exception as e:
            self.eLog("exception getPidTime: {}".format(str(e)))
            return "unknown"


    def getClassName(self):
        return self.__class__.__name__

    def getVersion(self):
        plugin_vsn_path = "{}.{}.__version__".format(PLUGIN_PKG, self._config.plugin)
        if is_module_available(plugin_vsn_path):
            vsn_mod = importlib.import_module(plugin_vsn_path)
            if hasattr(vsn_mod, "__version__"):
                return vsn_mod.__version__
        return "undefined"

    def getInfo(self):
        info = self._config._asdict()
        info["addr"] = self.getAddr()
        info["class"] = self.getClassName()
        info["vsn"] = self.getVersion()
        info["pid"] = self.getPid()
        info["start_time"] = self.getPidTime(self.getPid())
        info["work_dir"] = getWorkDir()
        info["logfile"] = self.getLogFile()
        return info



class Spawner(XSpawner): # NOQA


    @ApiHandler.route("/get_config")
    def _get_config(self, headers: dict, data: dict):
        return self._config._asdict()

    @ApiHandler.route("/backup_db")
    async def _backup_db(self, headers: dict, data: dict):
        self.iLog("{}::_backup_db BEG".format(self.__class__.__name__))
        if "conn" not in data:
            self.eLog("Failed to backup db, No conn in data")
            return False
        try:
            loop = self._ioloop.asyncio_loop
            rt = await loop.run_in_executor(None, backup_database, data["conn"])
            self.iLog("{}::_backup_db END {}".format(self.__class__.__name__, rt))
            return rt
        except Exception as e:
            self.eLog(f"Exception on backup db {e}")
            return False


    @ApiHandler.route("/drop_db")
    async def _drop_db(self, headers: dict, data: dict):
        self.iLog("{}::_drop_db BEG".format(self.__class__.__name__))
        if "conn" not in data:
            self.eLog("Failed to drop db, No conn in data")
            return False
        try:
            await drop_database(data["conn"])
            self.iLog("{}::_drop_db END".format(self.__class__.__name__))
        except Exception as e:
            self.eLog(f"Exception on drop db {e}")
            return False
        return True

    @ApiHandler.route("/get_children")
    async def _get_children(self, headers: dict, data: dict):
        return await self.getChildren()

    @ApiHandler.route("/start_child")
    async def _start_child(self, headers: dict, data: dict):
        self.iLog("{}::_start_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to start child, No id key in data {data}")
            return False

        model = await self.getConfig(data["id"])
        if model:
            child_config = model
        else:
            child_config = self._config._replace(**data)._replace(parent=self._config.id)

        # open systemed service
        if open_service(child_config):
            self.iLog(f"successfully start child {child_config}!")
        else:
            self.eLog(f"failed to start child {child_config}!")
            return False

        # wait child service ready
        child_addr = self.getLocalAddr(child_config.port)
        loop = self._ioloop.asyncio_loop
        ok = await loop.run_in_executor(None, wait_port_sync, child_config.port)
        if not ok:
            self.eLog(f"child {child_addr} did not become ready in 120s")
            return False
        self.iLog(f"child {child_addr} is ready")


        # open sub systemd service
        grand_children = await self.postJson(f"{child_addr}/get_children", {})
        if grand_children:
            for grand_child in grand_children:
                res = await self.postJson(f"{child_addr}/start_child", {"id":grand_child})
                if res:
                    self.iLog("Successful request to {}/start_child {}: {}".format(child_addr, grand_child, res))
                else:
                    self.eLog("Exception request to {}/start_child {}".format(child_addr, grand_child))
                    return False
                await tornado.gen.sleep(1)

        sts = get_service_status(self._config.host, data["id"])
        pid = int(sts["MainPID"]) if sts["ActiveState"] == "active" else None
        self.iLog(f"service status: {sts}")

        rt = {"id": data["id"], "pid": pid}
        self.iLog("{}::start_child END {}".format(self.__class__.__name__, rt))
        return rt

    @ApiHandler.route("/stop_child")
    async def _stop_child(self, headers: dict, data: dict):
        self.iLog("{}::_stop_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to stop child, No id key in data {data}")
            return False

        child_id = data["id"]
        model = await self.getConfig(child_id)

        if model:
            # close sub systemd service
            child_addr = self.getLocalAddr(model.port)
            grand_children = await self.postJson(f"{child_addr}/get_children", {})
            if grand_children:
                for grand_child in grand_children:
                    res = await self.postJson(f"{child_addr}/stop_child", {"id":grand_child})
                    if res:
                        self.iLog("Successful request to {}/stop_child {}".format(child_addr, grand_child))
                    else:
                        self.eLog("Exception request to {}/stop_child {}".format(child_addr, grand_child))
                        return False
                    await tornado.gen.sleep(0.2)
                await tornado.gen.sleep(0.5)

        sts = get_service_status(self._config.host, child_id)
        pid = int(sts["MainPID"]) if sts["ActiveState"] == "active" else None
        self.iLog(f"service status: {sts}")

        # close systemed service
        if close_service(self._config.host, child_id):
            if await self.delConfig(child_id):
                self.iLog(f"successfully rm model {child_id}")
            else:
                self.eLog(f"failed to rm model {child_id}!")
            rt = {"id": child_id, "pid": pid}
            self.iLog("{}::_stop_child END {}".format(self.__class__.__name__, rt))
            return rt
        else:
            self.eLog(f"failed to stop service {child_id}!")
            return False


    @ApiHandler.route("/deploy_child")
    async def _deploy_child(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        """
        一步部署并启动子服务。
        近支持直接上传文件: 传 fdata / fname / fargs
        参数:
          - fdata/fname/fargs: 与 /upload_plugin 一致，直接提供文件内容，且fargs中包含服务的 id & port。
        """
        self.iLog("{}::_deploy_child BEG".format(self.__class__.__name__))
        if fdata is None:
            self.eLog("Error: no plugin payload {}".format(fname))
            return False

        if fname is None:
            self.eLog("Error: no plugin name {}".format(fname))
            return False

        plugin_id = os.path.splitext(os.path.basename(fname))[0]

        if fargs is None:
            self.eLog("Error: no fargs")
            return False

        if "id" not in fargs:
            self.eLog("deploy_child: miss 'id' in fargs {}".format(fargs))
            return False

        child_id = fargs["id"]

        if "port" not in fargs:
            self.eLog("deploy_child: miss 'port' in fargs {}".format(fargs))
            return False

        child_port = int(fargs["port"])

        models = await self.getConfigs() or []
        for m in models:
            if m.id == child_id:
                self.eLog("Error: duplicated id {}".format(child_id))
                return False
            if m.port == child_port:
                self.eLog("Error: duplicated port {}".format(child_port))
                return False

        if not await self._upload_plugin(headers, fdata, fname, fargs):
            self.eLog("Error: failed to upload plugin {}".format(plugin_id))
            return False


        child_config = self._config._replace(
            parent=self._config.id,
            plugin=plugin_id,
            id=child_id,
            port=child_port,
            libs=fargs.get("libs", "") if fargs else ""
        )

        if not await self.addConfig(child_config):
            self.eLog("Error: failed to add model {}".format(child_id))
            return False

        rt = await self._start_child(headers, {"id": child_id})
        if rt and isinstance(rt, dict):
            self.iLog("{}::_deploy_child END {}".format(self.__class__.__name__, rt))
            return True
        else:
            await self.delConfig(child_id)
            self.eLog("Error: cleanup model {} after failure".format(child_id))
            return False


    @ApiHandler.route("/clean_plugin")
    async def _clean_plugin(self, headers: dict, data: dict):
        self.iLog("{}::_clean_plugin BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data or not data["plugin"]:
            self.wLog(f"Miss plugin in data {data}")
            return False

        srvapp = data["plugin"]

        if srvapp == "spawner" or srvapp == "administrator":
            self.wLog("refuse to clean spawner itself")
            return False

        pkgdir = f"{PLUGIN_PKG}.{srvapp}".replace('.', '/')
        if os.path.isdir(pkgdir):
            shutil.rmtree(pkgdir)
            self.iLog(f"directory {pkgdir} is deleted")
        else:
            modfile = pkgdir + ".py"
            if os.path.isfile(modfile):
                os.remove(modfile)
                self.iLog(f"file {modfile} is deleted")
            else:
                self.iLog(f"file {modfile} doesnt exist")

        mod = "{}.{}".format(PLUGIN_PKG, data["plugin"])
        if mod in sys.modules:
            del sys.modules[mod]
        mod = "{}.{}.{}".format(PLUGIN_PKG, data["plugin"], data["plugin"])
        if mod in sys.modules:
            del sys.modules[mod]
        mod = "test"
        if mod in sys.modules:
            del sys.modules[mod]

        self.iLog("{}::_clean_plugin END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/download_plugin")
    async def _download_plugin(self, headers: dict, data: dict):
        self.iLog("{}::_download_plugin BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data or not data["plugin"]:
            self.wLog(f"Miss plugin in data {data}")
            return False
        srvapp = data["plugin"]
        pkgdir = f"{PLUGIN_PKG}.{srvapp}".replace('.', '/')
        if os.path.isdir(pkgdir):
            fname = pkgdir + ".zip"
            try:
                zip_folder(pkgdir, fname, ["__pycache__", ".git", "logs"])
                self.iLog(f"directory {pkgdir} is zipped to {fname}")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, os.path.basename(fname)))
                return (fdata, os.path.basename(fname))
            finally:
                if os.path.exists(fname):
                    os.unlink(fname)
        else:
            fname = pkgdir + ".py"
            if os.path.isfile(fname):
                self.iLog(f"file {fname} is found")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, os.path.basename(fname)))
                return (fdata, os.path.basename(fname))
            else:
                self.wLog(f"file {fname} doesnt exist")
        self.iLog("{}::_download_plugin END".format(self.__class__.__name__))
        return False


    @ApiHandler.route("/upload_plugin")
    async def _upload_plugin(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        self.iLog("{}::_upload_plugin BEG {} {} {}".format(self.__class__.__name__, len(fdata), fname, fargs))
        fname = os.path.basename(fname)          # ← 入口统一 sanitize
        if get_file_type(fname) == "application/zip":
            zip_buffer = io.BytesIO(fdata)
            with zipfile.ZipFile(zip_buffer, 'r') as zipf:
                root = os.path.realpath(PLUGIN_DIR)
                for entry in zipf.namelist():
                    tgt = os.path.realpath(os.path.join(PLUGIN_DIR, entry))
                    if not tgt.startswith(root + os.sep):
                        self.eLog(f"zip entry escapes: {entry}")
                        return False
                zipf.extractall(PLUGIN_DIR)
                self.iLog(f"exact {fname} to {PLUGIN_DIR}")
        else:
            modfile = f"{PLUGIN_DIR}/{fname}"
            with open(modfile, "wb") as f:
                f.write(fdata)
                self.iLog(f"write to {modfile}")
        self.iLog("{}::_upload_plugin END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/test_child")
    async def _test_child(self, headers: dict, data: dict):
        self.iLog("{}::_test_child BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data \
        or "port" not in data \
        or "id" not in data:
            self.wLog(f"Miss plugin or port or id in data: {data}")
            return True
        
        test_dir = "{}/{}/tests".format(PLUGIN_DIR, data["plugin"])
        self.iLog("test_dir: {}".format(test_dir))
        if os.path.isdir(test_dir):
            await tornado.gen.sleep(1)
            if is_port_used(data["port"]):
                # run unittest
                loader = unittest.TestLoader()
                suite = loader.discover(start_dir=test_dir, top_level_dir=test_dir)
                runner = unittest.TextTestRunner(failfast=True)
                result = runner.run(suite)
                self.iLog("unittest result {}".format(result))
                if result.errors or result.failures:
                    self.eLog("unittest upon server {}={} failed.".format(data["plugin"], data["id"]))
                    return False
                else:
                    self.iLog("unittest passed.")
            else:
                self.wLog("server <:{}> is not running.".format(data["port"]))
                return False
        else:
            self.wLog("no unittest case.")
        self.iLog("{}::_test_child END".format(self.__class__.__name__))
        return True

    @ApiHandler.route("/get_info")
    async def _get_info(self, headers: dict, data: dict):
        return self.getInfo()


    def __init__(self, **kwargs):
        super().__init__(**kwargs)


##############################################################################
# Functions
##############################################################################

def js_to_dict(js):
    return tornado.escape.json_decode(js)


def dict_to_js(dt):
    return tornado.escape.json_encode(dt)


def _is_loopback_url(url: str) -> bool:
    """URL 是否指向本机环回地址。

    ⚠️ 安全假设：url 应由内部代码构造，不应直接透传用户输入。
    本函数仅用于决定是否关闭证书校验，不是 SSRF 防护。

    注：0.0.0.0 不在 loopback 范围（is_loopback=False），
    访问 https://0.0.0.0:port 时证书 SAN 不匹配、严格校验会失败，
    符合预期。
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    if host == "127.0.0.1":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_cert_for(url: str) -> bool:
    """内部调用（环回地址）关闭校验；外部调用严格校验。"""
    return not _is_loopback_url(url)


def _preview(body, limit: int = 200) -> bytes:
    """安全截取响应体片段，用于日志。body 可能为 None。"""
    return body[:limit] if body else b""


async def postAsyncFile(url: str, filepath: str, fileargs: dict = None):
    """POST 上传文件，返回解析后的 JSON 响应。

    参数:
      - url:      目标地址
      - filepath: 待上传文件路径
      - fileargs: 附加表单字段 {name: value}，value 会转为 str

    返回:
      - dict  成功
      - None  失败（文件不存在、非 200、JSON 解析失败等）
    """
    fileargs = fileargs or {}
    if not os.path.isfile(filepath):
        raise Exception("postAsyncFile: file not found: {filepath}")
        return None

    args = {k: [str(fileargs[k]).encode()] for k in fileargs}
    docs = make_docs({"file": [filepath]})

    boundary = tornado.escape.utf8(
        "----XSPAWNER" + uuid.uuid4().hex)
    body = make_multipart_form(boundary, args, docs)
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(
            boundary.decode()),
        # Content-Length 交给 Tornado 自动计算
    }

    # 使用 Tornado 全局单例（IOLoop 级别），复用连接池；
    # 依赖 xspawner 全局单 IOLoop 假设。
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(
            url, method="POST", headers=headers, body=body,
            validate_cert=_validate_cert_for(url),
        )
    except tornado.httpclient.HTTPClientError as e:
        raise Exception(f"postAsyncFile {url}: HTTP {e.code}: {e.message}; "
                     f"body={_preview(e.response.body) if e.response else b''!r}")
        return None
    except asyncio.CancelledError:
        raise Exception(f"postAsync {url}: CancelledError")
    except Exception as e:
        raise Exception(f"postAsyncFile {url}: "
                     f"{type(e).__name__}: {e}")
        return None

    if res.code != 200:
        raise Exception(f"postAsyncFile {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")
        return None

    rstr = res.body.decode("utf-8", errors="replace") if res.body else ""
    if not rstr:
        raise Exception(f"postAsyncFile {url}: empty response body")
        return None
    try:
        return json.loads(rstr)
    except json.JSONDecodeError as e:
        raise Exception(f"postAsyncFile {url}: invalid JSON: {e}; "
                     f"body={_preview(res.body)!r}")
        return None


async def postAsync(url, headers, body):
    """POST 请求，返回 parse_reply 的结果。

    返回:
      - dict/其他  成功（HTTP 200）
      - None       失败（非 200、解析失败、网络异常等）
    """
    # 使用 Tornado 全局单例（IOLoop 级别），复用连接池；
    # 依赖 xspawner 全局单 IOLoop 假设。
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(
            url, method="POST", headers=headers, body=body,
            validate_cert=_validate_cert_for(url),
            raise_error=False,        # 显式关闭：让下方 if res.code 生效
        )
    except asyncio.CancelledError:
        raise Exception(f"postAsync {url}: CancelledError")
    except Exception as e:
        raise Exception(f"postAsync {url}: {type(e).__name__}: {e}")
        return None

    if res.code != 200:
        raise Exception(f"postAsync {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")
        return None

    try:
        return parse_reply(res)
    except Exception as e:
        raise Exception(f"postAsync {url}: parse_reply failed: "
                     f"{type(e).__name__}: {e}; "
                     f"body={_preview(res.body)!r}")
        return None