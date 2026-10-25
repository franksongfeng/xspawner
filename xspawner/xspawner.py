# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import tornado.ioloop
import tornado.iostream
import tornado.gen
import tornado.queues
import tornado.concurrent
import tornado.web
import tornado.locks
import tornado.httpserver
import tornado.httpclient
import tornado.httputil
import tornado.tcpclient
from tortoise.exceptions import DoesNotExist

import pywebio.platform.tornado

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
import ssl
from urllib.parse import urlparse
from typing import Optional, List, Dict
from collections import UserDict

from .utilities.log import * # NOQA
from .utilities.client import * # NOQA
from .utilities.msg import * # NOQA
from .utilities.misc import * # NOQA
from .constants import * # NOQA
from .proxy import * #NOQA
from .orm import * #NOQA


INTERNAL_HANDLERS = ["PingPongHandler", "HomePageHandler", "ResourceHandler"]
CUSTOMED_HANDLERS = ["ApiHandler", "UiHandler"]

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



class State(UserDict):
    SerializableTypes = (str, int, float, bool, type(None))

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._validate_and_update(dict(*args, **kwargs))

    def __json__(self):
        return dict(self)
    
    def __setitem__(self, key, value):
        self._validate_value(value)
        super().__setitem__(key, value)

    def update(self, *args, **kwargs):
        self._validate_and_update(dict(*args, **kwargs))

    def _validate_and_update(self, new_data):
        for key, value in new_data.items():
            self._validate_value(value)
            super().__setitem__(key, value)

    def _validate_value(self, value):
        if isinstance(value, dict):
            for v in value.values():
                self._validate_value(v)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._validate_value(item)
        elif isinstance(value, self.SerializableTypes):
            pass
        else:
            raise TypeError(
                f"{type(value)} is not JSON serializable, just allow {self.SerializableTypes}"
            )

class SrvJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Config):
            return obj._asdict()
        if isinstance(obj, State):
            return obj.__json__()
        else:
            return super().default(obj)

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

    async def init_db(self):
        raise NotImplementedError

    async def use_proxy(self):
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
        self._server.listen(self._config.port, address=self._config.access)
        self.iLog("listening to port {}...".format(self._config.port))
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
        if self._fatal:                 # ← 必须有
            self.cLog("start END fatal, sys.exit(1)")
            sys.exit(1)
        self.iLog("start END")

    def stop(self):
        self.iLog("stop BEG")
        self.cLog("This instance {}:{} is stopping ...".format(self.__class__, self._config.port))
        self._ioloop.add_callback(self.del_route)
        self._server.stop()
        self._ioloop.stop()
        self.iLog("stop END")

    def getAddr(self):
        use_proxy = (self._config.access == "127.0.0.1") and (not self._config.ssl)
        if use_proxy:
            return "{}/xspawner/{}".format(self.getHostAddr(), self._config.id)
        else:
            return "{}:{}".format(self.getHostAddr(), self._config.port)

    def getLocalAddr(self, port=None):
        return "{}:{}".format(
            self.getLocalHostAddr(),
            port if port else self._config.port)


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
            self.cLog("init_db failed, service will be degraded!")
            self._fatal = True          # ← 不是 self.stop()
            self._server.stop()
            self._ioloop.stop()

    async def use_proxy(self):
        self.iLog("add_route BEG")
        res = (self._config.access == "127.0.0.1") and (not self._config.ssl) and await caddy_is_running()
        self.iLog(f"add_route END {res}")
        return res

    async def add_route(self):

        self.iLog("add_route BEG")
        if await self.use_proxy():
            ok = await caddy_add_route(self._config.id, self._config.port)
            self.iLog("caddy add route {}:{} {}".format(self._config.id, self._config.port, ok))
        self.iLog("add_route END")

    async def del_route(self):
        self.iLog("del_route BEG")
        if await self.use_proxy():
            ok = await caddy_del_route(self._config.id)
            self.iLog("caddy del route {} {}".format(self._config.id, ok))
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
                cfg_id=self._config.id
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
            self.eLog(f'getChildren EXP {e}')
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
        return await postAsync(url, headers, body)

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

    def getHostAddr(self):
        use_ssl = self._config.ssl
        use_proxy = (self._config.access == "127.0.0.1") and (not self._config.ssl)
        return "{}://{}".format(
            "https" if use_ssl or use_proxy else "http",
            self._config.host
        )

    def getLocalHostAddr(self):
        return "{}://{}".format(
            "https" if self._config.ssl else "http",
            "127.0.0.1"
        )


def search_for_class_in_file(fpath, class_name):
    print("search_for_class_in_file BEG {}".format(fpath))
    if get_file_type(fpath) == "text/x-python":
        mod_name = path_to_pkg(fpath)
        srv_cls = get_similar_cls(mod_name, class_name, 1)
        if srv_cls:
            print("search_for_class_in_file END {}".format(srv_cls))
            return srv_cls
    print("search_for_class_in_file END {}".format(None))

def search_for_class_in_package(fpath, class_name):
    print("search_for_class_in_package BEG {}".format(fpath))
    if get_file_type(fpath) == "application/zip":
        with zipfile.ZipFile(fpath, 'r') as zip_ref:
            zip_ref.testzip()

            # ── 校验所有条目都在 PLUGIN_DIR 内 ─────────────
            root = os.path.realpath(PLUGIN_DIR)
            for entry in zip_ref.namelist():
                tgt = os.path.realpath(os.path.join(PLUGIN_DIR, entry))
                if not tgt.startswith(root + os.sep):
                    print("zip entry escapes: {}".format(entry))
                    print("search_for_class_in_package END {}".format(None))
                    return None

            # ── 找到顶层包的 __init__.py 后整体解压 ────────
            for entry in zip_ref.namelist():
                print("entry {}".format(entry))
                if len(entry.split("/")) == 2 and os.path.basename(entry) == "__init__.py":
                    print("zip contains {}".format(entry))
                    zip_ref.extractall(PLUGIN_DIR)
                    print("{} is unzipped .".format(fpath))
                    pkg_name, _ = entry.split("/")
                    srv_cls = get_similar_cls(f"{PLUGIN_PKG}.{pkg_name}", class_name, 1)
                    if srv_cls:
                        print("search_for_class_in_package END {}".format(srv_cls))
                        return srv_cls
    print("search_for_class_in_package END {}".format(None))