# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.utilities.misc import * # NOQA
from xspawner.utilities.msg import * # NOQA
from xspawner.constants import * # NOQA
from xspawner.service import * # NOQA
from xspawner.xspawner import * # NOQA
from xspawner.orm import * # NOQA
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


    @ApiHandler.route("/get_config")
    def _get_config(self, headers: dict, data: dict):
        return self._config._asdict()

    @ApiHandler.route("/drop_db")
    async def _drop(self, headers: dict, data: dict):
        self.iLog("{}::_drop BEG {}".format(self.__class__.__name__, data))
        if "conn" not in data:
            self.eLog(f"Failed to drop db, No conn in data {data}")
            return False
        setting = parse_connection_str(data["conn"]) if isinstance(data["conn"], str) else data["conn"]
        await drop_database(setting)
        self.iLog("{}::_drop END")
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
        child_addr = self.getAddr(child_config.port)
        loop = self._ioloop.asyncio_loop
        ok = await loop.run_in_executor(None, wait_port_sync, child_config.port, child_config.host, 120)
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

        sts = get_service_status(data["id"])
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
            child_addr = self.getAddr(model.port)
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
        pid = int(sts["MainPID"]) if sts["ActiveState"] == "active" else None
        self.iLog(f"service status: {sts}")

        # close systemed service
        if close_service(child_id):
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

        plugin_id = os.path.basename(fname).split('.')[0]

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
            port=child_port
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
