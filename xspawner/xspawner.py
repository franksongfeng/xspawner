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
import tornado.httputil
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
import ssl
from typing import List

from .utilities.msg import * # NOQA
from .utilities.log import * # NOQA
from .utilities.misc import * # NOQA
from .common import * # NOQA
from .api_handler import * # NOQA
from .ui_handler import * # NOQA
from .flow_handler import * # NOQA
from .constants import * # NOQA


INTERNAL_HANDLERS = ["PingPongHandler", "HomePageHandler", "ResourceHandler", "ExitHandler"]
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
        self.render("index.html")

class ResourceHandler(tornado.web.StaticFileHandler):
    def set_default_headers(self):
        self.set_header('Cache-Control', 'max-age=0, no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')

class ExitHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("DELETE",)
    # terminate server
    def delete(self):
        gServer = XSpawner.getServer()
        assert gServer
        gServer.stop()
        self.write('stopped')


class XSpawner(Spawnable):
    _instance = None
    _config: Config = None
    _state: State = None
    _children: List[Config] = []
    _rq_count = 256

    # host & port is internal address
    # ioloop is the only main event loop
    # server is http server instance
    def __init__(self, config, state, children, **kwargs):
        ILine("__init__ BEG {} {}".format(config, kwargs))
        self._config = config
        self._state = state
        self._children = children

        # core queue
        self._req_queue = tornado.queues.Queue(self._rq_count)
        self._ioloop = tornado.ioloop.IOLoop.current()
        self._ioloop.add_callback(self.req_loop)

        handlers = []
        # user handlers are prior
        for path in ApiHandler.path_map:
            handlers.append((path, ApiHandler))
        for path in FlowHandler.path_map:
            handlers.append((path, FlowHandler))
        for path in UiHandler.path_map:
            handlers.append((path, UiHandler(path, self)))
        # fixed handlers are at the bottom
        resource_dir = RES_DIR_TEMP.format(config.app)
        handlers.extend([
            (r"/", HomePageHandler),
            (r"/resources/(.*)", ResourceHandler, {"path": resource_dir}),
            (r"/ping", PingPongHandler),
            (r"/stop", ExitHandler)
        ])
        # start http server
        app = tornado.web.Application(
            handlers=handlers,
            default_host="0.0.0.0"
        )
        if config.ssl:
            self._server = tornado.httpserver.HTTPServer(
                app,
                ssl_options=getSSLContext(config.certfile, config.keyfile)
            )
            ILine("httpserver is enhanced with ssl")
        else:
            self._server = tornado.httpserver.HTTPServer(app)
            ILine("httpserver is not enhanced with ssl")
        ILine("__init__ END")

    def postCallback(self, future, condition, res):
        if any([isinstance(res, t) for t in (str, bool, dict , int, float, list, tuple)]):
            if not future.done():
                future.set_result(res)
                condition.notify()
        else:
            # invalid res type
            raise Exception("invalid reply type")

    # consumer for queue
    async def req_loop(self):
        ILine("req_loop BEG")
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
                ILine("{} BEG".format(fun.__code__.co_name))
                return ()
            elif fun.__code__.co_argcount == 3: # for ApiHandler I (normal) or FlowHandler
                # transport JSON
                jdata = body.decode()
                ILine("{} BEG {}".format(fun.__code__.co_name, jdata))
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
                ILine("{} BEG [{}] {} {}".format(fun.__code__.co_name, len(fdata), fname, fargs))             
                return (fdata, fname, fargs)
            else:
                ELine('{}: invalid argument count {}'.format(fun.__code__.co_name, fun.__code__.co_argcount))


        async for (future, condition, path, headers, body) in self._req_queue:
            try:
                fun = ApiHandler.path_map[path]
                ct = headers["Content-Type"] if "Content-Type" in headers else None
                DLine("make_cbf_params {} {} {}".format(fun, ct, body))
                params = make_cbf_params(fun, ct, body)
                if isinstance(params, tuple):
                    res = await call_cbf(fun, self, headers, *params) if params else await call_cbf(fun, self)
                    if res is None:
                        CLine('{} return None, stop!'.format(fun.__code__.co_name))
                        self.stop()
                    else:
                        if isinstance(res, tuple):
                            fdata, fname = res
                            ILine("{} END [{}] {}".format(fun.__code__.co_name, len(fdata), fname))
                        else:
                            ILine("{} END {}".format(fun.__code__.co_name, res))
                    self.postCallback(future, condition, res)
                else:
                    # invalid args
                    self.stop()
            except Exception as e:
                WLine(traceback.format_exc())
                if not future.done():
                    ELine(repr(e))
                    future.set_result(False)
                    condition.notify()
            finally:
                self._req_queue.task_done()
        CLine("req_loop is going to stop...")
        self.stop()
        ILine("req_loop END")


    def start(self):
        ILine("start BEG")
        self._server.listen(self.getConfig().port)
        ILine("listening to port {}...".format(self.getConfig().port))
        self._ioloop.start()
        ILine("ioloop is started")
        self._ioloop.close()
        # free port after about 1 minute
        # import os, signal
        # os.kill(os.getpid(), signal.SIGTERM)
        ILine("start END")

    def stop(self):
        ILine("stop BEG")
        CLine("This instance {}:{} is stopping ...".format(self.__class__, self.getConfig().port))
        self._server.stop()
        self._ioloop.stop()
        ILine("stop END")

    def getAddr(self):
        return "{}://{}:{}".format(
            "https" if self.getConfig().ssl else "http",
            self.getConfig().host,
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
        ILine("{}::getServer BEG {}".format(cls.__name__, kwargs))
        if XSpawner._instance is None:
            XSpawner._instance = cls(
                **kwargs
            )
            ILine("{}::_instance is created".format(cls.__name__))
        ILine("{}::getServer END {}".format(cls.__name__, XSpawner._instance))
        return XSpawner._instance

    @classmethod
    def isChildClass(cls, kls):
        return issubclass(kls, cls) and kls is not cls

    @classmethod
    def getChildClass(cls, mod):
        return get_similar_cls(mod, cls.__name__, 2)

    @classmethod
    def search_for_server_cls(cls, fpath):
        ILine("search_for_server_cls BEG {}".format(fpath))
        if get_file_type(fpath) == PYTHON_MIME_TYPE:
            mod_name = path_to_pkg(fpath)
            srv_cls = cls.getChildClass(mod_name)
            if srv_cls:
                ILine("search_for_server_cls END {}".format(srv_cls))
                return srv_cls
        ILine("search_for_server_cls END {}".format(None))

    @classmethod
    def search_for_server_cls_in_pkg(cls, fpath):
        ILine("search_for_server_cls_in_pkg BEG {}".format(fpath))
        if get_file_type(fpath) == ZIP_MIME_TYPE:
            with zipfile.ZipFile(fpath, 'r') as zip_ref:
                zip_ref.testzip()
                for entry in zip_ref.namelist():
                    DLine("entry {}".format(entry))
                    if len(entry.split("/")) == 2 and os.path.basename(entry) == "__init__.py":
                        DLine("zip contains {}".format(entry))
                        zip_ref.extractall(APP_DIR)
                        DLine("{} is unzipped .".format(fpath))
                        pkg_name, _ = entry.split("/")
                        srv_cls = cls.getChildClass(f"{APP_PKG}.{pkg_name}")
                        if srv_cls:
                            ILine("search_for_server_cls_in_pkg END {}".format(srv_cls))
                            return srv_cls
        ILine("search_for_server_cls_in_pkg END {}".format(None))


    @staticmethod
    def testServer(url):
        ILine("testServer BEG {}".format(url))
        rt = syncReq(url, 'GET')
        ILine("testServer END {}".format(rt))
        return rt

    @staticmethod
    async def stopServer(url):
        ILine("stopServer BEG {}".format(url))
        rt = await asyncReq(url, 'DELETE')
        ILine("stopServer END {}".format(rt))
        return rt

    # jdata is dict or bool/int/list or None
    @staticmethod
    async def postJson(url, jdata):
        ILine("postJson BEG {} {}".format(url, jdata))
        headers = {
            "Content-Type": "application/json"
        }
        body = json.dumps(jdata) 
        rt = await postAsync(url, headers, body)
        ILine("postJson END {}".format(rt))
        return rt

    # fdata is bytes
    # fname is str
    # fargs is dict of {str:str}
    @staticmethod
    async def postForm(url, fdata, fname, fargs={}):
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
            ELine("exception getPidTime: {}".format(str(e)))
            return "unknown"


    def getClassName(self):
        return self.__class__.__name__

    def getInfo(self):
        info = self.getConfig()._asdict()
        info.update(self.getState())
        info["children"] = self.getChildren()
        info["class"] = self.getClassName()
        info["pid"] = self.getPid()
        info["start_time"] = self.getPidTime(self.getPid())
        info["work_dir"] = getWorkDir()
        info["logfile"] = self.getLogFile()
        return info

    def getAncestry(self):
        if not self.getConfig().ancestry:
            return []
        if isinstance(self.getConfig().ancestry, str):
            return list(json.loads(self.getConfig().ancestry))

    def getFather(self):
        if self.getAncestry():
            return self.getAncestry()[-1]