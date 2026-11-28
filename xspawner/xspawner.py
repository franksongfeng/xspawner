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
import ast
import traceback
import datetime
import functools
import zipfile
import uuid
import ssl
import signal
import socket
import unittest
import shutil
import io
import tempfile
from urllib.parse import urlparse
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
import pywebio.platform.tornado

# Self Libraries
from xspawner.log import Log # NOQA
from xspawner.constants import (
    Config, LOCAL_DB_URL, LOG_FILE, PLUGIN_DIR, PLUGIN_PKG, RES_DIR_TEMP,
)
from xspawner.orm import open_database, drop_database, backup_database, config_to_dict, value_to_config, is_config_data, set_state_table, StateModel #NOQA
from xspawner.service import open_service, close_service, get_service_status, upgrade_service # NOQA
from xspawner.proxy import _prefix, caddy_is_running, caddy_add_route, caddy_del_route  # NOQA
from xspawner.misc import getSSLContext, zip_folder, make_multipart_request, make_multipart_form, make_docs, parse_reply, get_file_type, is_module_available, is_port_used, getWorkDir, get_similar_cls # NOQA
from xspawner.validation import ( # NOQA
    RESERVED_PLUGIN_NAMES, is_valid_plugin_name, is_valid_port,
    is_valid_service_id, plugin_realpath,
)


##############################################################################
# Timeouts
##############################################################################

# 子服务就绪等待上限（秒）：_start_child / upgrade 通过 /get_info 判断就绪。
# socket activation 下端口在应用启动前就已监听，不能再用端口连通性判断。
SERVICE_READY_TIMEOUT = 120

# 服务间 HTTP 调用超时（秒）：必须 >= SERVICE_READY_TIMEOUT，并预留
# 子服务继续启动其孙服务的时间，否则父服务会先于子服务超时。
HTTP_REQUEST_TIMEOUT = 300

# 外部请求等待后台任务完成的上限（秒）：必须 >= HTTP_REQUEST_TIMEOUT。
API_WAIT_TIMEOUT = 360


def _service_pid(status: dict):
    """从 ``systemctl show`` 结果中取 MainPID。

    统一用 ``.get`` 读取：systemctl 失败时 ``get_service_status`` 只返回
    ``{'name', 'error'}``，直接下标会 KeyError。未运行或取值异常时返回 None。
    """
    if status.get("ActiveState") != "active":
        return None
    try:
        return int(status.get("MainPID") or 0)
    except (TypeError, ValueError):
        return None


def _ast_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _find_package_spawner_classes(pkg_root):
    """扫描包内子模块，找出直接继承 Spawner 的类：(模块, 类名)。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(pkg_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            if path == os.path.join(pkg_root, "__init__.py"):
                continue
            try:
                with open(path, encoding="utf-8",
                          errors="surrogateescape") as f:
                    tree = ast.parse(f.read(), filename=path)
            except Exception:
                continue
            rel = os.path.relpath(path, pkg_root)[:-3].replace(os.sep, ".")
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for base in node.bases:
                    name = _ast_name(base)
                    if name == "Spawner" or name.endswith(".Spawner"):
                        found.append((".{}".format(rel), node.name))
                        break
    return found


def _package_exports_spawner(pkg_root, candidates):
    """检查 __init__.py 是否已经导出候选 Spawner 类。"""
    init = os.path.join(pkg_root, "__init__.py")
    try:
        with open(init, encoding="utf-8", errors="surrogateescape") as f:
            tree = ast.parse(f.read(), filename=init)
    except Exception:
        return False

    candidate_set = set(candidates)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = _ast_name(base)
                if name == "Spawner" or name.endswith(".Spawner"):
                    return True
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
            target = os.path.join(
                pkg_root, *node.module.split(".")) + ".py"
            if not os.path.isfile(target):
                continue
            for alias in node.names:
                if (".{}".format(node.module), alias.name) in candidate_set:
                    return True
    return False


def _normalize_package_exports(pkg_root, stem):
    """包内类只在子模块、__init__.py 未导出时，生成最小导出语句。

    某些历史插件包的 __init__.py 只有版本号甚至引用了不存在的模块，
    XSpawner 在包顶层找不到 Spawner 类；这里根据子模块中的类补一条
    ``from .module import Class``，使包可以直接被加载。
    """
    candidates = _find_package_spawner_classes(pkg_root)
    if not candidates:
        return
    if _package_exports_spawner(pkg_root, candidates):
        return

    chosen = candidates[0]
    for candidate in candidates:
        if candidate[1].lower() == stem.lower():
            chosen = candidate
            break
    init = os.path.join(pkg_root, "__init__.py")
    with open(init, "w", encoding="utf-8") as f:
        f.write("# generated by xspawner: export package Spawner class\n")
        f.write("from {} import {}\n".format(chosen[0], chosen[1]))


def _stage_plugin_package(stem, ext, fdata):
    """把上传内容暂存为 <stem>/ 包目录，返回 staging 根目录。

    .py 会写入 <stem>/__init__.py；.zip 必须包含 <stem>/__init__.py。
    调用方负责把 staging/<stem> 移动到插件目录并覆盖旧版本。
    """
    root = os.path.realpath(PLUGIN_DIR)
    staging = tempfile.mkdtemp(
        prefix=".xspawner-{}-".format(stem), dir=root)
    try:
        pkg_stage = os.path.join(staging, stem)
        if ext == ".py":
            os.makedirs(pkg_stage)
            with open(os.path.join(pkg_stage, "__init__.py"), "wb") as f:
                f.write(fdata)
        elif ext == ".zip":
            with zipfile.ZipFile(io.BytesIO(fdata), "r") as zipf:
                names = zipf.namelist()
                if (stem + "/__init__.py") not in names:
                    raise ValueError(
                        "zip must contain {}/__init__.py".format(stem))
                for entry in names:
                    if not entry:
                        continue
                    try:
                        plugin_realpath(entry)
                    except ValueError:
                        raise ValueError(
                            "zip entry escapes: {}".format(entry))
                    normalized = os.path.normpath(entry)
                    if normalized != stem and not normalized.startswith(
                            stem + os.sep):
                        raise ValueError(
                            "zip entry not under {}/: {}".format(stem, entry))
                zipf.extractall(staging)
                if not os.path.isdir(pkg_stage):
                    raise ValueError(
                        "zip has no {} directory".format(stem))
                _normalize_package_exports(pkg_stage, stem)
        else:
            raise ValueError(
                "unsupported plugin file type: {}".format(ext))
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _plugin_class_available(stem):
    """重新导入 <stem>/ 包，检查其中是否存在 Spawner 子类。"""
    importlib.invalidate_caches()
    mod_name = "{}.{}".format(PLUGIN_PKG, stem)
    for name in list(sys.modules):
        if name == mod_name or name.startswith(mod_name + "."):
            sys.modules.pop(name, None)
    try:
        return get_similar_cls(mod_name, "Spawner", 1)
    except Exception:
        return None


def _take_activated_sockets():
    """取出 systemd socket activation 传进来的监听 socket。

    systemd 通过 LISTEN_PID/LISTEN_FDS 约定：socket 从 fd 3 开始连续排列。
    返回 socket 列表并清理环境变量，避免被子进程误继承。
    """
    listen_pid = os.environ.get("LISTEN_PID")
    listen_fds = os.environ.get("LISTEN_FDS")
    if not listen_pid or not listen_fds:
        return []
    try:
        if int(listen_pid) != os.getpid():
            return []
        count = int(listen_fds)
    except ValueError:
        return []

    socks = []
    for i in range(count):
        try:
            sock = socket.socket(fileno=3 + i)
        except OSError:
            continue
        sock.setblocking(False)
        socks.append(sock)

    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(key, None)
    return socks


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
    """插件静态文件处理器，挂载在 ``/static/(.*)``。

    映射关系::

        /static/<path>  ->  xspawner/plugins/<plugin>/static/<path>

    根目录在 ``XSpawner.__init__`` 中解析为绝对路径（``os.path.abspath``），
    因此不受服务工作目录变化影响；路径穿越、目录访问由
    ``StaticFileHandler`` 自身拦截（越界 403，目录 403，缺失 404）。

    注意：``/static/*`` 是公开可读的，和 administrator 的 UI 口令无关，
    不要在该目录放密钥等敏感文件。
    """

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

        cond_res = await condition.wait(
            timeout=datetime.timedelta(seconds=API_WAIT_TIMEOUT))
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
        value = await gServer.getValue(srv_id)
        if not is_config_data(value):
            self.set_status(404)
            self.write("Not Found Config of {srv_id}")
            return
        scheme = self.request.protocol
        upstream = "{}://127.0.0.1:{}/{}".format(scheme, value.get("port"), rest)
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
                body=(self.request.body if method in ("POST", "PUT", "PATCH")
                      else self.request.body or None),
                follow_redirects=False, raise_error=False,
                validate_cert=False, request_timeout=60)
        except Exception as e:
            gServer.eLog("proxy {} {} failed: {}".format(method, srv_id, e))
            self.set_status(502)
            self.write("Bad Gateway")
            return
        self.set_status(resp.code)
        for k, v in resp.headers.items():
            if k.lower() in ("content-length", "transfer-encoding", "connection", "set-cookie"):
                continue
            self.set_header(k, v)
        for cookie in resp.headers.get_list("Set-Cookie"):
            self.add_header("Set-Cookie", cookie)
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

    def getAddr(self):
        raise NotImplementedError

    def getLocalAddr(self, port):
        raise NotImplementedError

    def getConfig(self):
        raise NotImplementedError

    async def getValues(self):
        raise NotImplementedError

    async def getValue(self, key):
        raise NotImplementedError

    async def addValue(self, key, value):
        raise NotImplementedError

    async def setValue(self, key, value):
        raise NotImplementedError

    async def delValue(self, key):
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
        self._stopping = False
        self._graceful_stopping = False

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
        # 插件 static 子目录：/static/(.*) -> xspawner/plugins/<plugin>/static/(.*)
        # 解析成绝对路径，避免服务工作目录变化导致静态文件 404。
        static_dir = os.path.abspath(RES_DIR_TEMP.format(config.plugin))
        # fixed handlers are at the bottom
        handlers.extend([
            (r"/", HomePageHandler),
            (r"/static/(.*)", ResourceHandler, {"path": static_dir}),
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
            self._server = tornado.httpserver.HTTPServer(
                app,
                xheaders=(config.access == "127.0.0.1")     # 信任来自反向代理的头信息
            )
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
        self._stopping = False
        self._graceful_stopping = False
        self._install_signal_handlers()
        self._ioloop.add_callback(self.loop)
        self._ioloop.add_callback(self.add_kvs)
        self._ioloop.add_callback(self.add_route)

        activated = _take_activated_sockets()
        try:
            if activated:
                # systemd socket activation：监听 socket 由 systemd 持有，
                # 服务重启/升级期间端口不会关闭。
                self._server.add_sockets(activated)
                self.iLog(
                    "listening on systemd-activated socket(s) {}".format(
                        len(activated)))
            else:
                self._server.listen(
                    self._config.port, address=self._config.access)
                self.iLog(
                    "listening to port {}...".format(self._config.port))
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

    def _install_signal_handlers(self):
        # Tornado 6 的 IOLoop 包装了 asyncio loop，信号处理要注册到 asyncio loop 上。
        loop = getattr(self._ioloop, "asyncio_loop", None)
        if loop is None:
            loop = asyncio.get_event_loop()
        try:
            loop.add_signal_handler(signal.SIGTERM, self._graceful_stop)
            self.iLog("SIGTERM handler installed")
        except (NotImplementedError, RuntimeError, AttributeError) as e:
            self.wLog(f"cannot install SIGTERM handler: {e}")

    def _graceful_stop(self):
        """systemd stop/restart 时先停止 accept，再给在途请求留出排空时间。"""
        if getattr(self, "_graceful_stopping", False):
            return
        self._graceful_stopping = True
        self.wLog("SIGTERM received: stop accepting, drain in-flight requests")
        try:
            self._server.stop()
        except Exception:
            self.eLog(traceback.format_exc())
        try:
            grace = float(os.environ.get("XSPAWNER_SHUTDOWN_GRACE", "2"))
        except ValueError:
            grace = 2.0
        if grace < 0:
            grace = 0.0
        self.iLog(f"shutdown grace {grace}s")
        self._ioloop.call_later(grace, self._force_exit)

    def _force_exit(self):
        """排空后强制退出，避免非 daemon 线程拖住 systemd 的 stop 流程。"""

        async def _finish():
            try:
                await self.del_route()
            except Exception:
                self.eLog(traceback.format_exc())
            finally:
                os._exit(0)

        self._ioloop.add_callback(_finish)

    def stop(self):
        if getattr(self, "_stopping", False):
            return
        self._stopping = True
        self.iLog("stop BEG")
        self.cLog("This instance {}:{} is stopping ...".format(self.__class__, self._config.port))

        async def _shutdown():
            try:
                await self.del_route()
            except Exception:
                self.eLog(traceback.format_exc())
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
        self.iLog("getLocalAddr END")
        return addr

    def getPid(self):
        return os.getpid()

    async def add_kvs(self):
        self.iLog("add_kvs BEG")
        try:
            connection_str = LOCAL_DB_URL
            extra_model_mod = "{}.{}".format(PLUGIN_PKG, self._config.plugin)
            # 每个服务实例用自己的状态表 t_<服务标识>，必须在建表前设定
            set_state_table(self._config.id)
            await open_database(connection_str, extra_model_mod)
            # 服务自身的配置在 self._config 中；
            # 子服务的配置由父服务（一般是 administrator）创建时写入状态表。
            self.iLog("add_kvs END")
        except Exception as e:
            self.eLog(f"add_kvs raise {e}")
            self.cLog("add_kvs failed, no db => fatal")
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

    def getConfig(self) -> Config:
        """返回实例内部的配置元组 self._config。"""
        return self._config

    async def getValues(self) -> dict:
        """返回本服务状态表里的全部键值（含子服务配置行）。"""
        self.iLog(f"getValues BEG service={self._config.id}")
        try:
            models = await StateModel.all()
            rt = {m.id: m.value for m in models}
            self.iLog(f"getValues END {len(rt)}")
            return rt
        except Exception as e:
            self.eLog(f"getValues EXP: {e}")
            return {}

    async def getValue(self, key: str):
        """取一个键的值；键是本服务自己时返回自身配置。"""
        self.iLog(f"getValue BEG key={key}")
        if not key:
            self.wLog("getValue: empty key")
            return None
        if key == self._config.id:
            self.iLog("getValue END self")
            return self._config._asdict()
        try:
            model = await StateModel.get_or_none(id=key)
            if model is None:
                self.iLog(f"getValue END {key} no-hit")
                return None
            self.iLog(f"getValue END {key} type={type(model.value).__name__}")
            return model.value
        except Exception as e:
            self.eLog(f"getValue EXP {key}: {e}")
            return None


    async def addValue(self, key: str, value) -> bool:
        """新增一个键值；键已存在时不覆盖并返回 False。"""
        self.iLog(f"addValue BEG key={key} type={type(value).__name__}")
        if not key:
            self.wLog("addValue: empty key")
            return False
        try:
            await StateModel.create(
                id=key,
                value=value
            )
            self.iLog(f"addValue END {key} created")
            return True
        except Exception as e:
            self.eLog(f"addValue EXP {key}: {e}")
            return False

    async def setValue(self, key: str, value) -> bool:
        """写入一个键值；键不存在时新建，存在时覆盖。"""
        self.iLog(f"setValue BEG key={key} type={type(value).__name__}")
        if not key:
            self.wLog("setValue: empty key")
            return False
        try:
            model = await StateModel.get_or_none(id=key)
            if model:
                model.value = value
                await model.save()
                self.iLog(f"setValue END {key} updated")
            else:
                await StateModel.create(
                    id=key,
                    value=value
                )
                self.iLog(f"setValue END {key} created")
            return True
        except Exception as e:
            self.eLog(f"setValue EXP {key}: {e}")
            return False

    async def delValue(self, key: str) -> bool:
        """删除一个键值。"""
        self.iLog(f"delValue BEG key={key}")
        if not key:
            self.wLog("delValue: empty key")
            return False
        try:
            model = await StateModel.get_or_none(id=key)
            if model is None:
                self.iLog(f"delValue END {key} no-hit")
                return False
            await model.delete()
            self.iLog(f"delValue END {key} deleted")
            return True
        except Exception as e:
            self.eLog(f"delValue EXP {key}: {e}")
            return False


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
    async def postJson(self, url, jdata, timeout=HTTP_REQUEST_TIMEOUT):
        self.iLog("postJson BEG {} {}".format(url, jdata))
        headers = {
            "Content-Type": "application/json"
        }
        body = json.dumps(jdata) 
        rt = await postAsync(url, headers, body, timeout=timeout)
        self.iLog("postJson END {}".format(rt))
        return rt

    # fdata is bytes
    # fname is str
    # fargs is dict of {str:str}
    async def postForm(self, url, fdata, fname, fargs={}, timeout=HTTP_REQUEST_TIMEOUT):
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
        rt = await postAsync(url, headers, body, timeout=timeout)
        self.iLog(f"postForm END {rt}")
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
        values = await self.getValues()
        ids = [k for k, v in values.items()
               if is_config_data(v) and v.get("parent") == self._config.id]
        self.iLog("{}::_get_children END {}".format(self.__class__.__name__, ids))
        return ids

    @ApiHandler.route("/start_child")
    async def _start_child(self, headers: dict, data: dict):
        self.iLog("{}::_start_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to start child, No id key in data {data}")
            return False

        child_id = data["id"]
        if not is_valid_service_id(child_id):
            self.eLog(f"Failed to start child, invalid id: {child_id!r}")
            return False

        if child_id == self._config.id:
            # getValue 对本服务 id 返回 self._config 的 JSON，若放行会重启自身
            self.eLog(f"Failed to start child, id equals self: {child_id}")
            return False

        value = await self.getValue(child_id)
        if is_config_data(value):
            child_config = value_to_config(value)
        elif all(name in data for name in Config._fields):
            # /start_child 只有 {"id": ...} 时，该 id 必须已在状态表里登记；
            # 只有带完整 Config 字段的请求（/create、/deploy_child）才允许新建，
            # 否则会用父服务的端口/插件拼出一个假服务写进 systemd。
            child_config = self._config._replace(**data)._replace(parent=self._config.id)
        else:
            self.eLog(
                "Failed to start child, no stored config for {} and data is incomplete"
                .format(child_id))
            return False

        if not is_valid_service_id(child_config.id) \
                or not is_valid_plugin_name(child_config.plugin) \
                or not is_valid_port(child_config.port):
            self.eLog("Failed to start child, invalid config: {}".format(child_config))
            return False

        # 写入 unit 和等待 systemctl 都是同步操作。
        loop = self._ioloop.asyncio_loop
        if not await loop.run_in_executor(None, open_service, child_config):
            self.eLog(f"failed to start child {child_config}!")
            return False

        # 等待子服务真正可用：socket activation 下端口在应用启动前就已监听，
        # 不能用 wait_port_sync 判断就绪，必须以 /get_info 返回为准。
        child_addr = self.getLocalAddr(child_config.port)
        info = await self._wait_http_ready(
            child_addr, timeout=SERVICE_READY_TIMEOUT,
            service_id=child_id)
        if not isinstance(info, dict):
            self.eLog(
                f"child {child_addr} did not become ready "
                f"in {SERVICE_READY_TIMEOUT}s")
            return False
        self.iLog(f"child {child_addr} is ready")

        # 子服务不再自己写库，由父服务（一般是 administrator）在启动成功后登记。
        # 只登记带了完整 Config 字段的请求（如 /create、/deploy_child），
        # 避免 /start_child {"id": ...} 这类部分请求把拼出来的配置写进库。
        if not is_config_data(value) and all(name in data for name in Config._fields):
            expected = config_to_dict(child_config)
            if not await self.addValue(child_id, expected):
                # 并发请求可能已登记同一配置，回读确认后才能报告成功。
                if await self.getValue(child_id) != expected:
                    self.eLog(f"child {child_id} is running but its registration could not be confirmed")
                    return False


        # 启动孙服务：子服务刚启动时可能暂时不可达，失败只记录，不能让
        # 已经启动成功的本服务被误判为失败（否则调用方会清库/留下幽灵服务）。
        try:
            grand_children = await self.postJson(f"{child_addr}/get_children", {})
            if grand_children:
                for grand_child in grand_children:
                    res = await self.postJson(
                        f"{child_addr}/start_child", {"id": grand_child})
                    if res:
                        self.iLog("Successful start child {}".format(grand_child))
                    else:
                        self.eLog("Failed to start child {}".format(grand_child))
                    await tornado.gen.sleep(1)
        except Exception as e:
            self.eLog(
                "start grandchildren of {} via {} failed: {}: {}".format(
                    child_id, child_addr, type(e).__name__, e))

        rt = {"id": child_id, "pid": info.get("pid")}
        self.iLog("{}::start_child END {}".format(self.__class__.__name__, rt))
        return rt

    @ApiHandler.route("/upgrade_child")
    async def _upgrade_child(self, headers: dict, data: dict):
        """热升级子服务：systemd socket 保持监听，只重启服务进程。

        插件文件已更新到插件目录后调用；重启窗口内的新连接由 systemd
        socket 的内核队列接住，不会出现 connection refused。
        """
        self.iLog("{}::_upgrade_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to upgrade child, No id key in data {data}")
            return False

        child_id = data["id"]
        if not is_valid_service_id(child_id):
            self.eLog(f"Failed to upgrade child, invalid id: {child_id!r}")
            return False
        if child_id == self._config.id:
            self.eLog("Failed to upgrade child, id equals self")
            return False

        value = await self.getValue(child_id)
        if not is_config_data(value):
            self.eLog(f"Failed to upgrade child, no config for {child_id}")
            return False
        child_config = value_to_config(value)

        if not is_valid_service_id(child_config.id) \
                or not is_valid_plugin_name(child_config.plugin) \
                or not is_valid_port(child_config.port):
            self.eLog("Failed to upgrade child, invalid config")
            return False

        old_pid = _service_pid(get_service_status(child_id))

        if not upgrade_service(child_config):
            self.eLog(f"Failed to upgrade child {child_id}")
            return False

        child_addr = self.getLocalAddr(child_config.port)
        info = await self._wait_http_ready(
            child_addr, timeout=60, exclude_pid=old_pid,
            service_id=child_id)
        if not isinstance(info, dict):
            self.eLog(f"child {child_id} did not become ready after upgrade")
            return False

        rt = {"id": child_id, "pid": info.get("pid"),
              "addr": info.get("addr")}
        self.iLog("{}::_upgrade_child END {}".format(self.__class__.__name__, rt))
        return rt

    async def _wait_http_ready(self, addr, timeout=60, exclude_pid=None,
                               service_id=None):
        """轮询 /get_info，直到服务可用（不用端口判断：socket 始终在监听）。

        exclude_pid 用于升级场景：旧进程在优雅退出期间仍可能响应 /get_info，
        必须等到 PID 变化，才说明新进程已经接管。
        service_id 非空时同时检查 systemd 状态；服务进入 failed 后立即返回，
        避免在插件加载失败时干等完整超时。
        """
        deadline = time.time() + timeout
        last_status_check = 0.0
        loop = tornado.ioloop.IOLoop.current()
        while time.time() < deadline:
            if service_id and time.time() - last_status_check >= 2:
                last_status_check = time.time()
                try:
                    sts = await loop.run_in_executor(
                        None, get_service_status, service_id)
                except Exception:
                    sts = {}
                if sts.get("ActiveState") == "failed":
                    self.eLog(
                        f"service {service_id} failed while waiting for ready")
                    return None
            try:
                # 某些 HTTP 客户端（pycurl）在连接已排队、对端不响应时
                # request_timeout 不一定及时生效；这里再加一层硬超时，
                # 保证轮询间隔，避免热升级期间一直卡在旧连接上。
                probe = tornado.gen.convert_yielded(
                    self.postJson(f"{addr}/get_info", {}, timeout=3))
                try:
                    info = await tornado.gen.with_timeout(loop.time() + 3, probe)
                finally:
                    # with_timeout 不会取消原任务，下一轮探测前先等待它退出。
                    if probe.cancel():
                        try:
                            await probe
                        except asyncio.CancelledError:
                            pass
            except Exception:
                info = None
            if isinstance(info, dict):
                if exclude_pid is None or info.get("pid") != exclude_pid:
                    return info
            await tornado.gen.sleep(0.5)
        return None

    @ApiHandler.route("/stop_child")
    async def _stop_child(self, headers: dict, data: dict):
        self.iLog("{}::_stop_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to stop child, No id key in data {data}")
            return False

        child_id = data["id"]
        if not is_valid_service_id(child_id):
            self.eLog(f"Failed to stop child, invalid id: {child_id!r}")
            return False

        if child_id == self._config.id:
            # 与 _start_child 一致：禁止停掉当前服务自身
            self.eLog(f"Failed to stop child, id equals self: {child_id}")
            return False

        value = await self.getValue(child_id)

        if is_config_data(value):
            # close sub systemd service
            child_addr = self.getLocalAddr(value.get("port"))
            try:
                grand_children = await self.postJson(f"{child_addr}/get_children", {})
            except Exception as e:
                # 子服务已停止/不可达：跳过孙服务处理，继续停服和清理
                self.wLog(f"get_children from {child_addr} failed: {e}")
                grand_children = None
            if grand_children:
                for grand_child in grand_children:
                    res = await self.postJson(f"{child_addr}/stop_child", {"id":grand_child})
                    if res:
                        self.iLog("Successful stop child {}".format(grand_child))
                    else:
                        self.eLog("Failed to stop child {}".format(grand_child))
                        return False
                    await tornado.gen.sleep(0.2)
                await tornado.gen.sleep(0.5)

        sts = get_service_status(child_id)
        pid = _service_pid(sts)
        self.iLog(f"service status: {sts}")

        # close systemed service
        if close_service(child_id):
            if await self.delValue(child_id):
                self.iLog(f"successfully rm value {child_id}")
            else:
                self.eLog(f"failed to rm value {child_id}!")
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
        if not is_valid_plugin_name(plugin_id):
            self.eLog("deploy_child: invalid plugin name in file name: {!r}".format(plugin_id))
            return False

        if fargs is None:
            self.eLog("Error: no fargs")
            return False

        if "id" not in fargs:
            self.eLog("deploy_child: miss 'id' in fargs {}".format(fargs))
            return False

        child_id = fargs["id"]
        if not is_valid_service_id(child_id):
            self.eLog("deploy_child: invalid id: {!r}".format(child_id))
            return False

        if "port" not in fargs:
            self.eLog("deploy_child: miss 'port' in fargs {}".format(fargs))
            return False

        if not is_valid_port(fargs["port"]):
            self.eLog("deploy_child: invalid port: {!r}".format(fargs["port"]))
            return False
        child_port = int(fargs["port"])

        # 服务自身的配置不在状态表中，重复性需先与自己比对
        if child_id == self._config.id:
            self.eLog("Error: duplicated id {}".format(child_id))
            return False
        if child_port == self._config.port:
            self.eLog("Error: duplicated port {}".format(child_port))
            return False

        values = await self.getValues()
        for key, value in values.items():
            if key == child_id:
                self.eLog("Error: duplicated id {}".format(child_id))
                return False
            if is_config_data(value) and value.get("port") == child_port:
                self.eLog("Error: duplicated port {}".format(child_port))
                return False

        if not await self._install_plugin(
                fdata, fname, fargs, validate=True):
            self.eLog("Error: failed to upload plugin {}".format(plugin_id))
            return False


        child_config = self._config._replace(
            parent=self._config.id,
            plugin=plugin_id,
            id=child_id,
            port=child_port,
            libs=fargs.get("libs", "") if fargs else ""
        )

        if not await self.addValue(child_config.id, config_to_dict(child_config)):
            self.eLog("Error: failed to add value {}".format(child_id))
            return False

        rt = None
        try:
            rt = await self._start_child(headers, {"id": child_id})
        except Exception as e:
            self.eLog("deploy_child: start {} raised {}: {}".format(
                child_id, type(e).__name__, e))
        finally:
            # 无论 _start_child 返回 False 还是抛异常，都清掉刚写入的配置，
            # 避免状态表里留下没有对应服务的记录。
            if not (rt and isinstance(rt, dict)):
                if await self.delValue(child_id):
                    self.iLog("deploy_child: cleaned value {}".format(child_id))
                else:
                    self.eLog("deploy_child: failed to clean value {}".format(child_id))

        if rt and isinstance(rt, dict):
            self.iLog("{}::_deploy_child END {}".format(self.__class__.__name__, rt))
            return True
        return False


    @ApiHandler.route("/clean_plugin")
    async def _clean_plugin(self, headers: dict, data: dict):
        self.iLog("{}::_clean_plugin BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data or not data["plugin"]:
            self.wLog(f"Miss plugin in data {data}")
            return False

        srvapp = data["plugin"]
        if not is_valid_plugin_name(srvapp):
            self.eLog(f"refuse to clean invalid plugin name: {srvapp!r}")
            return False
        if srvapp in RESERVED_PLUGIN_NAMES:
            self.wLog("refuse to clean spawner itself")
            return False

        try:
            pkgdir = plugin_realpath(srvapp)
            modfile = plugin_realpath(srvapp + ".py")
        except ValueError as e:
            self.eLog(f"refuse to clean plugin {srvapp!r}: {e}")
            return False

        if os.path.isdir(pkgdir):
            shutil.rmtree(pkgdir)
            self.iLog(f"directory {pkgdir} is deleted")
        elif os.path.isfile(modfile):
            os.remove(modfile)
            self.iLog(f"file {modfile} is deleted")
        else:
            self.iLog(f"file {modfile} doesnt exist")

        # 清掉已加载的模块（含子模块），下次导入时重新读文件。
        prefix = "{}.{}".format(PLUGIN_PKG, srvapp)
        for name in list(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                del sys.modules[name]
        sys.modules.pop("test", None)

        self.iLog("{}::_clean_plugin END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/download_plugin")
    async def _download_plugin(self, headers: dict, data: dict):
        self.iLog("{}::_download_plugin BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data or not data["plugin"]:
            self.wLog(f"Miss plugin in data {data}")
            return False
        srvapp = data["plugin"]
        if not is_valid_plugin_name(srvapp):
            self.eLog(f"refuse to download invalid plugin name: {srvapp!r}")
            return False

        try:
            pkgdir = plugin_realpath(srvapp)
            modfile = plugin_realpath(srvapp + ".py")
            zippath = plugin_realpath(srvapp + ".zip")
        except ValueError as e:
            self.eLog(f"refuse to download plugin {srvapp!r}: {e}")
            return False

        if os.path.isdir(pkgdir):
            try:
                zip_folder(pkgdir, zippath, ["__pycache__", ".git", "logs"])
                self.iLog(f"directory {pkgdir} is zipped to {zippath}")
                with open(zippath, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, os.path.basename(zippath)))
                return (fdata, os.path.basename(zippath))
            finally:
                if os.path.exists(zippath):
                    os.unlink(zippath)
        elif os.path.isfile(modfile):
            self.iLog(f"file {modfile} is found")
            with open(modfile, 'rb') as f:
                fdata = f.read()
            self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, os.path.basename(modfile)))
            return (fdata, os.path.basename(modfile))
        else:
            self.wLog(f"file {modfile} doesnt exist")
        self.iLog("{}::_download_plugin END".format(self.__class__.__name__))
        return False


    @ApiHandler.route("/upload_plugin")
    async def _upload_plugin(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        # /upload_plugin 是通用上传接口，保持原有 4 参数签名；
        # 需要校验/回滚的创建、升级、部署走 _install_plugin(validate=True)。
        return await self._install_plugin(
            fdata, fname, fargs, validate=False)

    async def _install_plugin(self, fdata: bytes, fname: str, fargs: dict,
                              validate: bool = False,
                              expected_class: str = None):
        self.iLog("{}::_install_plugin BEG {} {} {}".format(self.__class__.__name__, len(fdata), fname, fargs))
        fname = os.path.basename(fname)          # ← 入口统一 sanitize
        stem, ext = os.path.splitext(fname)
        ext = ext.lower()
        if not is_valid_plugin_name(stem):
            self.eLog(f"refuse to upload invalid plugin name: {fname!r}")
            return False
        if ext not in (".py", ".zip"):
            self.eLog(
                f"refuse to upload unsupported plugin file: {fname!r} "
                f"(expect .py or .zip)")
            return False

        try:
            pkgdir = plugin_realpath(stem)
            modfile = plugin_realpath(stem + ".py")
        except ValueError as e:
            self.eLog(f"refuse to write plugin {fname!r}: {e}")
            return False

        # 统一安装为同名包目录：
        #   - 单文件模块写入 <stem>/__init__.py；
        #   - zip 必须提供 <stem>/__init__.py。
        # 先写 staging，再整体覆盖目的目录，避免 .py 与同名目录互相遮蔽。
        try:
            staging = _stage_plugin_package(stem, ext, fdata)
        except (ValueError, zipfile.BadZipFile, OSError) as e:
            self.eLog(f"refuse plugin {fname!r}: {e}")
            return False

        backup_dir = None

        def _restore_backup():
            if os.path.isdir(pkgdir):
                shutil.rmtree(pkgdir)
            if os.path.isfile(modfile):
                os.remove(modfile)
            if backup_dir:
                old_pkg = os.path.join(backup_dir, stem)
                old_mod = os.path.join(backup_dir, stem + ".py")
                if os.path.isdir(old_pkg):
                    shutil.move(old_pkg, pkgdir)
                if os.path.isfile(old_mod):
                    shutil.move(old_mod, modfile)

        try:
            if validate:
                # 升级/创建带校验时先备份旧版本；新包没有 Spawner 类则回滚。
                backup_dir = tempfile.mkdtemp(
                    prefix=".xspawner-old-", dir=os.path.realpath(PLUGIN_DIR))
                if os.path.isdir(pkgdir):
                    shutil.move(pkgdir, os.path.join(backup_dir, stem))
                if os.path.isfile(modfile):
                    shutil.move(modfile, os.path.join(backup_dir, stem + ".py"))

            # 改写前删除整个旧包目录（连同其中所有文件和子目录），
            # 然后创建全新的包目录，避免任何旧内容残留。
            if os.path.isdir(pkgdir) and not os.path.islink(pkgdir):
                shutil.rmtree(pkgdir)
            elif os.path.exists(pkgdir):
                os.remove(pkgdir)
            if os.path.isfile(modfile):
                os.remove(modfile)
            os.makedirs(pkgdir)

            staged = os.path.join(staging, stem)
            for entry in os.listdir(staged):
                shutil.move(os.path.join(staged, entry),
                            os.path.join(pkgdir, entry))

            if validate:
                new_cls = _plugin_class_available(stem)
                reason = None
                if new_cls is None:
                    reason = "no Spawner class"
                elif expected_class and new_cls.__name__ != expected_class:
                    reason = "class name mismatch: old={} new={}".format(
                        expected_class, new_cls.__name__)
                if reason:
                    _restore_backup()
                    self.eLog(
                        "plugin {} rejected: {}; old version restored".format(
                            stem, reason))
                    return False
        except Exception as e:
            try:
                _restore_backup()
            except Exception:
                self.eLog(
                    "restore plugin {} failed: {}".format(
                        stem, traceback.format_exc()))
            self.eLog(
                "install plugin package {} failed: {}: {}".format(
                    pkgdir, type(e).__name__, e))
            return False
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if backup_dir:
                shutil.rmtree(backup_dir, ignore_errors=True)

        self.iLog(f"installed plugin package {pkgdir}")
        self.iLog("{}::_install_plugin END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/test_child")
    async def _test_child(self, headers: dict, data: dict):
        self.iLog("{}::_test_child BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data \
        or "port" not in data \
        or "id" not in data:
            self.wLog(f"Miss plugin or port or id in data: {data}")
            return True

        if not is_valid_plugin_name(data["plugin"]) \
                or not is_valid_service_id(data["id"]) \
                or not is_valid_port(data["port"]):
            self.eLog(f"invalid plugin/id/port in data: {data}")
            return False

        try:
            test_dir = plugin_realpath(data["plugin"], "tests")
        except ValueError as e:
            self.eLog(f"invalid test dir: {e}")
            return False
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


async def postAsyncFile(url: str, filepath: str, fileargs: dict = None,
                        timeout: float = HTTP_REQUEST_TIMEOUT):
    """POST 上传文件，返回解析后的 JSON 响应。

    参数:
      - url:      目标地址
      - filepath: 待上传文件路径
      - fileargs: 附加表单字段 {name: value}，value 会转为 str
      - timeout:  整个请求的超时（秒），默认 HTTP_REQUEST_TIMEOUT，
                  必须不小于子服务就绪等待（SERVICE_READY_TIMEOUT）

    返回:
      - dict  成功（HTTP 200）

    失败时（文件不存在、非 200、网络异常、JSON 解析失败等）抛出 Exception。
    """
    fileargs = fileargs or {}
    if not os.path.isfile(filepath):
        raise Exception(f"postAsyncFile: file not found: {filepath}")

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
            raise_error=False,        # 显式关闭：让下方 if res.code 生效
            request_timeout=timeout,
        )
    except asyncio.CancelledError:
        raise Exception(f"postAsyncFile {url}: CancelledError")
    except Exception as e:
        raise Exception(f"postAsyncFile {url}: "
                     f"{type(e).__name__}: {e}")

    if res.code != 200:
        raise Exception(f"postAsyncFile {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")

    rstr = res.body.decode("utf-8", errors="replace") if res.body else ""
    if not rstr:
        raise Exception(f"postAsyncFile {url}: empty response body")
    try:
        return json.loads(rstr)
    except json.JSONDecodeError as e:
        raise Exception(f"postAsyncFile {url}: invalid JSON: {e}; "
                     f"body={_preview(res.body)!r}")


async def postAsync(url, headers, body, timeout: float = HTTP_REQUEST_TIMEOUT):
    """POST 请求，返回 parse_reply 的结果。

    参数:
      - timeout: 整个请求的超时（秒），默认 HTTP_REQUEST_TIMEOUT，
                 必须不小于子服务就绪等待（SERVICE_READY_TIMEOUT）

    返回:
      - dict/其他  成功（HTTP 200）

    失败时（非 200、解析失败、网络异常等）抛出 Exception。
    """
    # 使用 Tornado 全局单例（IOLoop 级别），复用连接池；
    # 依赖 xspawner 全局单 IOLoop 假设。
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(
            url, method="POST", headers=headers, body=body,
            validate_cert=_validate_cert_for(url),
            raise_error=False,        # 显式关闭：让下方 if res.code 生效
            request_timeout=timeout,
        )
    except asyncio.CancelledError:
        raise Exception(f"postAsync {url}: CancelledError")
    except Exception as e:
        raise Exception(f"postAsync {url}: {type(e).__name__}: {e}")

    if res.code != 200:
        raise Exception(f"postAsync {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")

    try:
        return parse_reply(res)
    except Exception as e:
        raise Exception(f"postAsync {url}: parse_reply failed: "
                     f"{type(e).__name__}: {e}; "
                     f"body={_preview(res.body)!r}")
