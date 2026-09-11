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
import io
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
        self._reports.update(report)

    @ApiHandler.route("/get_config")
    def _get_config(self, headers: dict, data: dict):
        return self.getConfig()._asdict()

    @ApiHandler.route("/get_children")
    async def _get_children(self, headers: dict, data: dict):
        return await self.getChildren()


    @ApiHandler.route("/start_child")
    async def _start_child(self, headers: dict, data: dict):
        self.iLog("{}::_start_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to start child, No id key in data {data}") 
            return False

        model = await self.getModel(data["id"])
        if model:
            child_config = Config(**model)
        else:
            child_config = self.getConfig()._replace(**data)._replace(parent=self.getConfig().id)

        # open systemed service
        if open_service(child_config):
            self.iLog(f"successfully start child {child_config}!")
        else:
            self.eLog(f"failed to start child {child_config}!")
            return False

        # wait child service ready
        child_addr = self.getAddr(child_config.port)
        loop = self._ioloop.asyncio_loop
        ok = await loop.run_in_executor(None, _wait_port_sync, child_config.port)
        if not ok:
            self.eLog(f"child {child_addr} did not become ready in 30s")
            return False
        self.iLog(f"child {child_addr} is ready")

        # add report
        if self.getConfig().reportup:
            srvaddr = self.getAddr(child_config.port)
            self.addFlow(f"{srvaddr}/report/state?interval=1", self.on_state)

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

        sts = get_service_status(data["id"])
        pid = sts["pid"]
        self.iLog(f"service status: {sts}")

        rt = {"id": data["id"], "pid": int(pid) if pid is not None else None}
        self.iLog("{}::start_child END {}".format(self.__class__.__name__, rt))
        return rt

    @ApiHandler.route("/stop_child")
    async def _stop_child(self, headers: dict, data: dict):
        self.iLog("{}::_stop_child BEG {}".format(self.__class__.__name__, data))
        if "id" not in data:
            self.eLog(f"Failed to stop child, No id key in data {data}")
            return False

        child_id = data["id"]
        model = await self.getModel(child_id)

        if model:
            # close sub systemd service
            child_addr = self.getAddr(model["port"])
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

        sts = get_service_status(child_id)
        pid = sts["pid"]
        self.iLog(f"service status: {sts}")

        # close systemed service
        if close_service(child_id):
            if await self.delModel(child_id):
                self.iLog(f"successfully rm model {child_id}")
            else:
                self.eLog(f"failed to rm model {child_id}!")
            rt = {"id": child_id, "pid": int(pid) if pid is not None else None}
            self.iLog("{}::_stop_child END {}".format(self.__class__.__name__, rt))
            return rt
        else:
            self.eLog(f"failed to stop service {child_id}!")
            return False

    @ApiHandler.route("/clean_plugin")
    async def _clean_plugin(self, headers: dict, data: dict):
        self.iLog("{}::_clean_plugin BEG {}".format(self.__class__.__name__, data))
        if "plugin" not in data or not data["plugin"]:
            self.wLog(f"Miss plugin in data {data}")
            return False

        srvapp = data["plugin"]

        if srvapp == "spawner" or srvapp == "supervisor":
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
            fname = srvapp + ".zip"
            try:
                zip_folder(pkgdir, fname, ["__pycache__", ".git", "logs"])
                self.iLog(f"directory {pkgdir} is zipped to {fname}")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, fname))
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
                self.dLog("{}::_download_plugin END {}".format(self.__class__.__name__, fname))
                return (fdata, fname)
            else:
                self.wLog(f"file {fname} doesnt exist")
        self.iLog("{}::_download_plugin END".format(self.__class__.__name__))
        return False


    @ApiHandler.route("/upload_plugin")
    async def _upload_plugin(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        self.iLog("{}::_upload_plugin BEG {} {} {}".format(self.__class__.__name__, len(fdata), fname, fargs))
        if "plugin" in fargs:
            srvapp = fargs["plugin"]
        else:
            srvapp = os.path.splitext(os.path.basename(fname))[0]
        if get_file_type(fname) == "application/zip":
            zip_buffer = io.BytesIO(fdata)
            with zipfile.ZipFile(zip_buffer, 'r') as zipf:
                zipf.extractall(PLUGIN_DIR)
                self.iLog(f'exact {fname} to {PLUGIN_DIR}')
        else:
            modfile = f"{PLUGIN_DIR}/{fname}"
            with open(modfile, "wb") as f:
                f.write(fdata)
                self.iLog(f'write to {modfile}')
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

    @FlowHandler.route("/report/state")
    def _report_state(self, headers: dict, data: dict):
        reports = self.getReports()

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


def _wait_port_sync(port, host="127.0.0.1", timeout=30, interval=0.5):

    """同步探测 TCP 端口是否可连接，简单粗暴"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            pass
        time.sleep(interval)
    return False
