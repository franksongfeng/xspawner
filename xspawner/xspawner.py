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
from .utilities.msg import syncReq, asyncReq, postAsync # NOQA
from .utilities.log import DLine, ILine, WLine, ELine, CLine # NOQA
from .utilities.misc import make_multipart_request, get_file_type, get_child_cls # NOQA
from .serviceable import Serviceable, Config, State # NOQA
from . import RES_DIR_TEMP # NOQA
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

FIXED_HANDLERS = ["PingHandler", "MainHandler", "StaticFileHandler", "StopHandler"]
USER_HANDLERS = ["Reaction", "Interaction", "Circulation"]

class PingHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET",)
    # ping/pong test
    def get(self):
        self.write('pong')
        self.finish()

class MainHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("GET",)
    def get(self):
        self.set_header("Content-Type", "text/html")
        self.render("index.html")

class StaticFileHandler(tornado.web.StaticFileHandler):
    def set_default_headers(self):
        self.set_header('Cache-Control', 'max-age=0, no-cache, no-store, must-revalidate')
        self.set_header('Pragma', 'no-cache')
        self.set_header('Expires', '0')

class StopHandler(tornado.web.RequestHandler):
    SUPPORTED_METHODS = ("DELETE",)
    # terminate server
    def delete(self):
        gServer = XSpawner.getServer()
        assert gServer
        gServer.stop()
        self.write('stopped')

class Reaction(tornado.web.RequestHandler):
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


class Interaction:
    path_map = {}

    def __new__(cls, path, srv):
        f = cls.path_map[path]
        WIOHandler = pywebio.platform.tornado.webio_handler(
            functools.partial(f, srv),
            reconnect_timeout=3,
            check_origin=False
        )
        class InteractionHandler(WIOHandler):
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
        return InteractionHandler

    @classmethod
    def route(cls, path):
        def decorator(f):
            cls.path_map[path] = f
            return f
        return decorator

class Circulation(tornado.web.RequestHandler):
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


class XSpawner(Serviceable):
    _instance = None
    _config: Config = None
    _state: State = None
    _children: list =[]
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
        for path in Reaction.path_map:
            handlers.append((path, Reaction))
        for path in Circulation.path_map:
            handlers.append((path, Circulation))
        for path in Interaction.path_map:
            handlers.append((path, Interaction(path, self)))
        # fixed handlers are at the bottom
        resource_dir = RES_DIR_TEMP.format(self._config.app)
        handlers.extend([
            (r"/", MainHandler),
            (r"/resources/(.*)", StaticFileHandler, {"path": resource_dir}),
            (r"/ping", PingHandler),
            (r"/stop", StopHandler)
        ])
        # start http server
        app = tornado.web.Application(
            handlers=handlers,
            default_host="0.0.0.0"
        )
        if "security" in kwargs and kwargs["security"]:
            self._server = tornado.httpserver.HTTPServer(
                app,
                ssl_options=self.getSSLContext(mtls=False, **kwargs)
            )
        else:
            self._server = tornado.httpserver.HTTPServer(app)
        ILine("__init__ END")

    def getSSLOptions(self, mtls, **kwargs):
        if "certfile" in kwargs and "keyfile" in kwargs:
            if os.path.isfile(kwargs["certfile"]) and os.path.isfile(kwargs["keyfile"]):
                ssl_opts = {
                    "certfile": kwargs["certfile"],
                    "keyfile": kwargs["keyfile"]
                }
                if mtls and "ca_certs" in kwargs:
                    if os.path.isfile(kwargs["ca_certs"]):
                        ssl_opts["cert_reqs"] = ssl.CERT_REQUIRED
                        ssl_opts["ca_certs"] = kwargs["ca_certs"]
                return ssl_opts

    def getSSLContext(self, mtls, **kwargs):
        if "certfile" in kwargs and "keyfile" in kwargs:
            if os.path.isfile(kwargs["certfile"]) and os.path.isfile(kwargs["keyfile"]):
                ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                ssl_ctx.load_cert_chain(certfile=kwargs["certfile"], keyfile=kwargs["keyfile"])
                # Optional: Require client certificates
                if mtls and "ca_certs" in kwargs:
                    if os.path.isfile(kwargs["ca_certs"]):
                        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
                        ssl_ctx.load_verify_locations(kwargs["ca_certs"])  # CA to verify client certs
                else:
                    ssl_ctx.verify_mode = ssl.CERT_NONE
                return ssl_ctx

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
            if fun.__code__.co_argcount == 1: # for Interaction
                ILine("{} BEG".format(fun.__code__.co_name))
                return ()
            elif fun.__code__.co_argcount == 3: # for Reaction I (normal) or Circulation
                # transport JSON
                jdata = body.decode()
                ILine("{} BEG {}".format(fun.__code__.co_name, jdata))
                return (json.loads(jdata),)
            elif fun.__code__.co_argcount == 5: # for Reaction II (upload)
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
                fun = Reaction.path_map[path]
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
        self._server.listen(self._config.port)
        ILine("listening to port {}...".format(self._config.port))
        self._ioloop.start()
        ILine("ioloop is started")
        self._ioloop.close()
        # free port after about 1 minute
        # import os, signal
        # os.kill(os.getpid(), signal.SIGTERM)
        ILine("start END")

    def stop(self):
        ILine("stop BEG")
        CLine("This instance {}:{} is stopping ...".format(self.__class__, self._config.port))
        self._server.stop()
        self._ioloop.stop()
        ILine("stop END")

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
        return get_child_cls(mod, cls.__name__)

    @staticmethod
    def testService(url):
        ILine("postJson BEG {}".format(url))
        rt = syncReq(url, 'GET')
        ILine("postJson END {}".format(rt))
        return rt

    @staticmethod
    async def stopService(url):
        ILine("stopService BEG {}".format(url))
        rt = await asyncReq(url, 'DELETE')
        ILine("stopService END {}".format(rt))
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
