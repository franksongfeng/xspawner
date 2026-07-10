# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.utilities.misc import * # NOQA
from xspawner.utilities.msg import * # NOQA
from xspawner.constants import * # NOQA
from xspawner.service import * # NOQA
from xspawner.xspawner import * # NOQA
import tornado.gen
import tornado.queues
import tornado.httpclient
import requests
from requests.exceptions import RequestException
import psutil

import inspect
import importlib
import unittest
import os
import sys
import json
import traceback
import hashlib
import uuid
import zipfile
import tempfile
import shutil
import subprocess
import socket
import time
import signal
import mimetypes
import datetime
import random


##############################################################################
# Constants and Variables and Classes
##############################################################################

class Spawner(XSpawner): # NOQA
    _reports = dict()

    def getReports(self):
        return self._reports

    def setReport(self, report):
        return self._reports.update(report)

    @ApiHandler.route("/get_config")
    def _get_config(self, headers: dict, data: dict):
        return self.getConfig()._asdict()


    @ApiHandler.route("/get_state")
    def _get_state(self, headers: dict, data: dict):
        return dict(self.getState())

    @ApiHandler.route("/set_state")
    def _set_state(self, headers: dict, data: dict):
        self.setState(data)
        return True

    @ApiHandler.route("/get_children")
    def _get_children(self, headers: dict, data: dict):
        return self.getChildren()

    @ApiHandler.route("/add_child")
    async def _add_child(self, headers: dict, data: dict):
        self.iLog("{}::_add_child BEG {}".format(self.__class__.__name__, data))
        if "name" not in data \
        or "addr" not in data:
            self.eLog(f"Failed to add child, miss name or addr in data {data}")
            return False
        srvaddr = data["addr"]
        self.iLog(f"{srvaddr} is connectable")
        self.addChild({"name": data["name"], "addr": data["addr"]})
        # add reportup flow
        if self.getConfig().reportup:
            self.addFlow(f"{srvaddr}/report/state?interval=1", self.on_state)
        self.iLog("{}::_add_child END".format(self.__class__.__name__))
        return True

    @ApiHandler.route("/start_child")
    async def _start_child(self, headers: dict, data: dict):
        self.iLog("{}::_start_child BEG {}".format(self.__class__.__name__, data))
        if "port" not in data \
        or "name" not in data \
        or "app" not in data:
            self.eLog(f"Failed to start child, miss port or name or app in data {data}")
            return False

        srvancestry = "{}:{}".format(self.getConfig().name, self.getConfig().port)
        child_config = self.getConfig()._replace(port=data["port"], name=data["name"], app=data["app"], ancestry=srvancestry)
        rt = open_service(child_config)
        self.iLog(f"open_service: {rt}")
        if "success" in rt and not rt["success"]:
            self.eLog(f"failed to start child {child_config.name}!")
            return False

        await tornado.gen.sleep(1)

        sts = get_service_status(data["name"])
        pid = sts["pid"]
        self.iLog(f"service status: {sts}")

        pkgdir = "{}/{}".format(APP_DIR, data["app"])
        if os.path.exists(pkgdir):
            pkgfname = "{}/{}.py".format(pkgdir, data["app"])
        else:
            pkgfname = "{}.py".format(pkgdir)
        srvcls = search_for_class_in_file(pkgfname, "Spawner")

        srvaddr = "{}:{}".format(
            self.getHostAddr(),
            data["port"])
        new_srv = {"name": data["name"], "app": data["app"], "cls": srvcls.__name__, "pid": int(pid), "addr": srvaddr}
        self.iLog("{}::_start_child END {}".format(self.__class__.__name__, new_srv))
        return new_srv

    @ApiHandler.route("/stop_child")
    async def _stop_child(self, headers: dict, data: dict):

        self.iLog("{}::_stop_child BEG {}".format(self.__class__.__name__, data))
        if "name" not in data:
            self.eLog(f"Miss name in data {data}")
            return False

        child_name = data["name"]

        elm = self.getChild(child_name)
        if not elm:
            self.wLog("cannot find child server on {}".format(data))
            return False

        self.delChild(child_name)

        if "addr" not in elm:
            self.eLog("cannot find addr in elm {}".format(elm))
            return False

        child_addr = elm["addr"]

        res = await self.postJson(f"{child_addr}/get_info", {})
        if res is None:
            self.eLog("Exception request to {}/get_info".format(child_addr))
            return False

        grand_children = await self.postJson(f"{child_addr}/get_children", {})
        if grand_children is None:
            self.eLog("Exception request to {}/get_children".format(child_addr))
            return False

        for grand_child in grand_children:
            await tornado.gen.sleep(0.2)
            res = await self.postJson(f"{child_addr}/stop_child", {"name":grand_child["name"]})
            if res is None:
                self.eLog("Exception request to {}/stop_child".format(child_addr))
                return False

        await tornado.gen.sleep(0.5)

        rt = close_service(child_name)
        self.iLog(f"delete service: {rt}")

        self.iLog("{}::_stop_child END".format(self.__class__.__name__))
        return True

    @ApiHandler.route("/clean_app")
    async def _clean_app(self, headers: dict, data: dict):
        self.iLog("{}::_clean_app BEG {}".format(self.__class__.__name__, data))
        if "app" not in data or not data["app"]:
            self.wLog(f"Miss app in data {data}")
            return False

        srvapp = data["app"]
        pkgdir = f"{APP_PKG}.{srvapp}".replace('.', '/')
        if os.path.exists(pkgdir) and srvapp != "spawner":
            shutil.rmtree(pkgdir)
            self.iLog(f"directory {pkgdir} is deleted")
        else:
            modfile = pkgdir + ".py"
            if os.path.isfile(modfile):
                os.remove(modfile)
                self.iLog(f"file {modfile} is deleted")
            else:
                self.iLog(f"file {modfile} doesnt exist")

        mod = "{}.{}".format(APP_PKG, data["app"])
        if mod in sys.modules:
            del sys.modules[mod]
        mod = "{}.{}.{}".format(APP_PKG, data["app"], data["app"])
        if mod in sys.modules:
            del sys.modules[mod]
        mod = "test"
        if mod in sys.modules:
            del sys.modules[mod]

        self.iLog("{}::_clean_app END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/download_app")
    async def _download_app(self, headers: dict, data: dict):
        self.iLog("{}::_download_app BEG {}".format(self.__class__.__name__, data))
        if "app" not in data or not data["app"]:
            self.wLog(f"Miss app in data {data}")
            return False
        srvapp = data["app"]
        pkgdir = f"{APP_PKG}.{srvapp}".replace('.', '/')
        if os.path.exists(pkgdir):
            fname = srvapp + ".zip"
            try:
                zip_folder(pkgdir, fname, ["__pycache__", ".git", "logs"])
                self.iLog(f"directory {pkgdir} is zipped to {fname}")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_app END {}".format(self.__class__.__name__, fname))
                return (fdata, fname)
            finally:
                if os.path.exists(fname):
                    os.unlink(fname)
        else:
            fname = pkgdir + ".py"
            if os.path.isfile(fname):
                self.iLog(f"file {fname} is found")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_app END {}".format(self.__class__.__name__, fname))
                return (fdata, fname)
            else:
                self.wLog(f"file {fname} doesnt exist")
        self.iLog("{}::_download_app END".format(self.__class__.__name__))
        return False


    @ApiHandler.route("/upload_app")
    async def _upload_app(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        self.iLog("{}::_upload_app BEG {} {} {}".format(self.__class__.__name__, len(fdata), fname, fargs))
        if "app" in fargs:
            srvapp = data["app"]
        else:
            srvapp = os.path.splitext(os.path.basename(fname))[0]
        if get_file_type(fname) == "application/zip":
            zip_buffer = io.BytesIO(fdata)
            with zipfile.ZipFile(zip_buffer, 'r') as zipf:
                zipf.extractall(APP_DIR)
                self.iLog(f'exact {fname} to {APP_DIR}')
        else:
            modfile = f"{APP_DIR}/{fname}"
            with open(modfile, "wb") as f:
                f.write(fdata)
                self.iLog(f'write to {modfile}')
        self.iLog("{}::_upload_app END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/test_child")
    async def _test_child(self, headers: dict, data: dict):
        self.iLog("{}::_test_child BEG {}".format(self.__class__.__name__, data))
        if "app" not in data \
        or "port" not in data \
        or "name" not in data:
            self.wLog(f"Miss app or port or name in data: {data}")
            return True
        
        test_dir = "{}/{}/tests".format(APP_DIR, data["app"])
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
                    self.eLog("unittest upon server {}={} failed.".format(data["app"], data["name"]))
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

    @FlowHandler.route("/report/state")
    def _report_state(self, headers: dict, data: dict):
        reports = self.getReports()
        reports[self.getConfig().name] = self.getState().__json__()

        evt = {
            "event": "message",
            "data": json.dumps(reports, separators=(',', ':'), ensure_ascii=False)
        }
        return evt

    def on_state(self, chunk):
        event = parse_sse_event(chunk.decode('utf-8'))
        self.setReport(event["data"])

    def addFlow(self, srvurl, cb):
        self.iLog("{}::addFlow BEG {}".format(self.__class__.__name__, srvurl))
        async def connect(srvurl):
            client = tornado.httpclient.AsyncHTTPClient(force_instance=True)
            request = tornado.httpclient.HTTPRequest(
                url=srvurl,
                method="GET",
                streaming_callback=cb,
                request_timeout=0,
                headers={
                    'Accept': 'text/event-stream',
                    'Cache-Control': 'no-cache'
                }
            )
            try:
                await client.fetch(request)
            except Exception as e:
                self.eLog('Flow {} is disconnected, exception {}:{}'.format(srvurl, e.__class__.__name__, e))
            finally:
                client.close()
        self._ioloop.add_callback(connect, srvurl)
        self.iLog("{}::addFlow END".format(self.__class__.__name__))

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
