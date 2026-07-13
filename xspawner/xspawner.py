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
import pywebio.platform.tornado

import os
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
from typing import List
from collections import namedtuple, UserDict

from .utilities.log import * # NOQA
from .utilities.client import * # NOQA
from .utilities.msg import * # NOQA
from .utilities.misc import * # NOQA
from .constants import * # NOQA


Config = namedtuple('Config', ['name', 'plugin', 'host', 'port', 'access', 'ancestry', 'reportup', 'log', 'severity', 'ssl', 'certfile', 'keyfile'])


INTERNAL_HANDLERS = ["PingPongHandler", "HomePageHandler", "ResourceHandler"]
CUSTOMED_HANDLERS = ["ApiHandler", "UiHandler", "FlowHandler"]

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
        self.finsih()

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "X-Requested-With, Content-Type")
        self.set_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")

    async def post(self):
        """ handle http/post request with data in body"""
        from xspawner import XSpawner # NOQA
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
        from xspawner import XSpawner # NOQA
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
                    self.set_header('Content-Type', 'text/html; charset=utf-8')
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


class FlowHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET", "OPTIONS")
    path_map = {}

    # CORS
    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()

    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "Content-Type")
        self.set_header("Access-Control-Allow-Methods", "GET, OPTIONS")

    def initialize(self):
        self.set_header('Content-Type', 'text/event-stream')
        self.set_header('Cache-Control', 'no-cache')
        self.set_header('Connection', 'keep-alive')

    def on_finish(self):
        # Ensure the client disconnects when the handler is finished
        if not self._finished:
            self.finish()

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
        interval = float(self.get_argument('interval', default="1.0"))
        assert (isinstance(interval, int) or isinstance(interval, float)) and (interval > 0)
        # no Content-Type in self.request.headers
        # self.request.body should be empty
        from xspawner import XSpawner # NOQA
        gServer = XSpawner.getServer()
        assert gServer
        if path in self.path_map:
            f, varnames = self.path_map[path]
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
                    elif evt is False:
                        print(f"stream is closed from server {path}")
                        self.finish()
                        return
                    else:
                        pass
                    await tornado.gen.sleep(interval)
            except Exception as e:
                print(traceback.format_exc())
                print("stream is closed on exception: {}".format(str(e)))
            self.finish()
            return

    @classmethod
    def route(cls, path):
        def decorator(f):
            varnames = f.__code__.co_varnames[1:] if len(f.__code__.co_varnames) > 1 else ()
            cls.path_map[path] = (f, varnames)
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

    def __init__(self, config: Config, state: State, children: List[Config], **others):
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def getPid(self):
        raise NotImplementedError

    def getAddr(self):
        raise NotImplementedError

    def getConfig(self):
        raise NotImplementedError

    def getState(self):
        raise NotImplementedError

    def setState(self, data):
        raise NotImplementedError

    def getChildren(self):
        raise NotImplementedError

    def getChild(self, name):
        raise NotImplementedError

    def delChild(self, name):
        raise NotImplementedError

    def addChild(self, child):
        raise NotImplementedError


class XSpawner(Spawnable):
    _instance = None
    _logger = None
    _config: Config = None
    _state: State = None
    _children: List[Config] = []

    # host is external access address
    # port is internal listening port
    # _config is immutable boot parameters for _instance
    # _ioloop is the only main event loop
    # _server is http server instance
    # _logger is logging instance
    # _instance is xspawner singleton service
    # _children is the subordinate services spawned by service
    # _state is internal state in service
    def __init__(self, config, state, children, **kwargs):
        print("__init__ BEG {} {}".format(config, kwargs))
        self._config = config
        self._state = state
        self._children = children

        # core queue
        self._req_queue = tornado.queues.Queue(256)
        self._ioloop = tornado.ioloop.IOLoop.current()
        self._ioloop.add_callback(self.loop)

        # use CurlHTTPClient for more stable Connection
        tornado.httpclient.AsyncHTTPClient.configure(
            "tornado.curl_httpclient.CurlAsyncHTTPClient"
        )

        # start logger
        self._logger = Log(
            config.name,
            "file",
            self.getLogFile(),
            config.severity
        )

        # inform ancestry to add child
        if config.ancestry:
            try:
                ancestry_service, ancestry_port = parse_ancestry(config.ancestry)
                ancestry_addr = "{}:{}".format(self.getHostAddr(), ancestry_port)
                response_json = requests.post(ancestry_addr + "/add_child", json={"name": config.name, "addr": self.getAddr()})
                self.iLog(f"add child response {response_json}")
            except Exception as e:
                self.eLog('Exception on adding child request {}:{}'.format(e.__class__.__name__, e))

        handlers = []
        # user handlers are prior
        for path in ApiHandler.path_map:
            handlers.append((path, ApiHandler))
        for path in FlowHandler.path_map:
            handlers.append((path, FlowHandler))
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
            default_host="localhost"
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
                self.iLog("{} BEG".format(fun.__code__.co_name))
                return ()
            elif fun.__code__.co_argcount == 3: # for ApiHandler I (normal) or FlowHandler
                # transport JSON
                jdata = body.decode()
                self.iLog("{} BEG {}".format(fun.__code__.co_name, jdata))
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
                self.iLog("{} BEG [{}] {} {}".format(fun.__code__.co_name, len(fdata), fname, fargs))
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
        self._server.listen(self.getConfig().port, address=self.getConfig().access)
        self.iLog("listening to port {}...".format(self.getConfig().port))
        self._ioloop.start()
        self.iLog("ioloop is started")
        self._ioloop.close()
        self.iLog("start END")

    def stop(self):
        self.iLog("stop BEG")
        self.cLog("This instance {}:{} is stopping ...".format(self.__class__, self.getConfig().port))
        self._server.stop()
        self._ioloop.stop()
        self.iLog("stop END")

    def getAddr(self):
        return "{}:{}".format(
            self.getHostAddr(),
            self.getConfig().port)

    def getPid(self):
        return os.getpid()

    def getConfig(self):
        return self._config

    def getState(self):
        return self._state

    def setState(self, data):
        return self._state.update(data)

    def getChildren(self):
        return self._children

    def getChild(self, name):
        return search_list_of_dict(
            self.getChildren(),
            "name",
            name
        )

    def delChild(self, name):
        child = self.getChild(name)
        if child:
            self.getChildren().remove(child)

    def addChild(self, child):
        if child:
            self.getChildren().append(child)


    # implement singleton
    @classmethod
    def getServer(cls,**kwargs):
        print("{}::getServer BEG {}".format(cls.__name__, kwargs))
        if XSpawner._instance is None:
            XSpawner._instance = cls(
                **kwargs
            )
            print("{}::_instance is created".format(cls.__name__))
        print("{}::getServer END {}".format(cls.__name__, XSpawner._instance))
        return XSpawner._instance

    @classmethod
    def isChildClass(cls, kls):
        return issubclass(kls, cls) and kls is not cls

    async def testServer(self, port, host="localhost", ssl=False):
        self.iLog("testServer BEG {} {} {}".format(port, host, ssl))
        tcp_client = tornado.tcpclient.TCPClient()
        try:
            stream = await tcp_client.connect(host, port, ssl_options=ssl.create_default_context() if ssl else None)
            stream.close()  # connect succeed and clost it
            self.iLog("testServer END true")
            return True
        except Exception as e:
            self.eLog(f'WARN: url {url} is not connected: {e}')
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

    def getLogFile(self):
        return LOG_FILE_TEMP.format(self.getConfig().name)

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
        plugin_vsn_path = "{}.{}.__version__".format(PLUGIN_PKG, self.getConfig().plugin)
        if is_module_available(plugin_vsn_path):
            vsn_mod = importlib.import_module(plugin_vsn_path)
            if hasattr(vsn_mod, "__version__"):
                return vsn_mod.__version__
        return "undefined"

    def getInfo(self):
        info = self.getConfig()._asdict()
        info.update(self.getState())
        info["children"] = self.getChildren()
        info["class"] = self.getClassName()
        info["vsn"] = self.getVersion()
        info["pid"] = self.getPid()
        info["start_time"] = self.getPidTime(self.getPid())
        info["work_dir"] = getWorkDir()
        info["logfile"] = self.getLogFile()
        return info

    def getHostAddr(self):
        return "{}://{}".format(
            "https" if self.getConfig().ssl else "http",
            self.getConfig().host
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

def parse_ancestry(s: str):
    if ':' in s:
        a, b = s.split(':', 1)
        return a.strip(), b.strip()
    else:
        return s
