# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.utilities.log import * # NOQA
from xspawner.utilities.misc import * # NOQA
from xspawner import XSpawner # NOQA
from xspawner.constants import * # NOQA
from xspawner.serviceable import * # NOQA
from xspawner.api_handler import ApiHandler # NOQA
import tornado.gen
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


##############################################################################
# Constants and Variables and Classes
##############################################################################
BASIC_CMD = "sudo python3 -u -m xspawner --name {} --app {} --host {} --port {} --severity {} --ancestry {}"


class Spawner(XSpawner): # NOQA


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

    @ApiHandler.route("/start_child")
    async def _start_child(self, headers: dict, data: dict):
        DLine("{}::_start_child BEG {}".format(self.__class__.__name__, data))
        if "port" not in data \
        or "severity" not in data \
        or "name" not in data \
        or "app" not in data:
            ELine("Miss port or severity or name or app in data")
            return False

        ancestry = self.getAncestry()
        ancestry.append(
            (
                self.getConfig().name,
                self.getAddr()
            )
        )
        srvancestry = json.dumps(ancestry, cls=SrvJSONEncoder, separators=(',', ':'))
        ILine(f"srvancestry: {srvancestry}")
        cmd = BASIC_CMD.format(data["name"], data["app"], self.getConfig().host, data["port"], data["severity"], srvancestry)

        # add SSL options
        srvsecurity = self.getConfig().security
        srvcertfile = self.getConfig().certfile
        srvkeyfile = self.getConfig().keyfile
        if srvsecurity and srvcertfile and srvkeyfile:
            cmd += " --security --certfile {} --keyfile {}".format(srvcertfile, srvkeyfile)
            ILine("security options are passed to child server")

        ILine(f"start server command: {cmd}")
        pid = start_background_process(cmd.split())
        if not pid:
            ELine("no pid")
            return False

        srvname = data["name"]
        srvapp = data["app"]
        srvport = data["port"]
        pkgdir = "{}/{}".format(APP_DIR, srvapp)
        if os.path.exists(pkgdir):
            pkgfname = "{}/{}.py".format(pkgdir, srvapp)
        else:
            pkgfname = "{}.py".format(pkgdir)
        srvcls = XSpawner.search_for_server_cls(pkgfname)

        srvvsn = "undefined"
        if is_module_available(f"{SYSTEM_ID}.apps.{srvapp}.__version__"):
            mod = importlib.import_module(f"{SYSTEM_ID}.apps.{srvapp}.__version__")
            if hasattr(mod, "__version__"):
                srvvsn = mod.__version__

        srvaddr = "http://{}:{}".format(self.getConfig().host, srvport)
        self.addChild({"name": srvname, "addr": srvaddr})
        new_srv = {"name": srvname, "app": srvapp, "cls": srvcls.__name__, "vsn": srvvsn, "pid": int(pid), "addr": srvaddr}
        DLine("{}::_start_child END {}".format(self.__class__.__name__, new_srv))
        return new_srv

    @ApiHandler.route("/stop_child")
    async def _stop_child(self, headers: dict, data: dict):
        DLine("{}::_stop_child BEG {}".format(self.__class__.__name__, data))
        if "name" not in data:
            ELine(f"Miss name in data {data}")
            return False

        srvname = data["name"]

        elm = search_list_of_dict(
            self.getChildren(),
            "name",
            srvname
        )
        if not elm:
            WLine("cannot find child server on {}".format(data))
            return False

        if "addr" not in elm:
            ELine("cannot find addr in elm {}".format(elm))
            return False

        srvaddr = elm["addr"]

        res = await self.postJson(f"{srvaddr}/get_info", {})

        srvpid = res["pid"]

        self.delChild(elm)

        try:
            os.kill(int(srvpid), signal.SIGTERM)
        except Exception as e:
            CLine(traceback.format_exc()) # NOQA
            WLine("server <{} :{}> dont exist.".format(srvname, srvpid))
            return False

        DLine("{}::_stop_child END".format(self.__class__.__name__))
        return True

    @ApiHandler.route("/clean_app")
    async def _clean_app(self, headers: dict, data: dict):
        DLine("{}::_clean_app BEG {}".format(self.__class__.__name__, data))
        if "app" not in data or not data["app"]:
            WLine(f"Miss app in data {data}")
            return False

        srvapp = data["app"]
        pkgdir = f"{APP_PKG}.{srvapp}".replace('.', '/')
        if os.path.exists(pkgdir) and srvapp != "spawner":
            shutil.rmtree(pkgdir)
            ILine(f"directory {pkgdir} is deleted")
        else:
            modfile = pkgdir + ".py"
            if os.path.isfile(modfile):
                os.remove(modfile)
                ILine(f"file {modfile} is deleted")
            else:
                ILine(f"file {modfile} doesnt exist")
        DLine("{}::_clean_app END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/download_app")
    async def _download_app(self, headers: dict, data: dict):
        DLine("{}::_download_app BEG {}".format(self.__class__.__name__, data))
        if "app" not in data or not data["app"]:
            WLine(f"Miss app in data {data}")
            return False
        srvapp = data["app"]
        pkgdir = f"{APP_PKG}.{srvapp}".replace('.', '/')
        if os.path.exists(pkgdir):
            fname = srvapp + ".zip"
            try:
                zip_folder(pkgdir, fname, ["__pycache__", ".git", "logs"])
                ILine(f"directory {pkgdir} is zipped to {fname}")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                DLine("{}::_download_app END {}".format(self.__class__.__name__, fname))
                return (fdata, fname)
            finally:
                if os.path.exists(fname):
                    os.unlink(fname)
        else:
            fname = pkgdir + ".py"
            if os.path.isfile(fname):
                ILine(f"file {fname} is found")
                with open(fname, 'rb') as f:
                    fdata = f.read()
                DLine("{}::_download_app END {}".format(self.__class__.__name__, fname))
                return (fdata, fname)
            else:
                WLine(f"file {fname} doesnt exist")
        DLine("{}::_download_app END".format(self.__class__.__name__))
        return False


    @ApiHandler.route("/upload_app")
    async def _upload_app(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        DLine("{}::_upload_app BEG {} {} {}".format(self.__class__.__name__, len(fdata), fname, fargs))
        if "app" in fargs:
            srvapp = data["app"]
        else:
            srvapp = os.path.splitext(os.path.basename(fname))[0]
        if get_file_type(fname) == "application/zip":
            zip_buffer = io.BytesIO(fdata)
            with zipfile.ZipFile(zip_buffer, 'r') as zipf:
                zipf.extractall(APP_DIR)
                ILine(f'exact {fname} to {APP_DIR}')
        else:
            modfile = f"{APP_DIR}/{fname}"
            with open(modfile, "wb") as f:
                f.write(fdata)
                ILine(f'write to {modfile}')
        DLine("{}::_upload_app END".format(self.__class__.__name__))
        return True


    @ApiHandler.route("/test_child")
    async def _test_cases(self, headers: dict, data: dict):
        DLine("{}::_test_cases BEG {}".format(self.__class__.__name__, data))
        if "app" not in data \
        or "port" not in data \
        or "pid" not in data:
            WLine(f"Miss app or port or pid in data: {data}")
            return True
        
        test_dir = "{}/{}/tests".format(APP_DIR, data["app"])
        ILine("test_dir: {}".format(test_dir))
        if os.path.isdir(test_dir):
            await tornado.gen.sleep(1)
            if is_port_used(data["port"]):
                # run unittest
                loader = unittest.TestLoader()
                suite = loader.discover(start_dir=test_dir, top_level_dir=test_dir)
                runner = unittest.TextTestRunner(failfast=True)
                result = runner.run(suite)
                ILine("unittest result {}".format(result))
                if result.errors or result.failures:
                    if is_port_used(data["port"]):
                        os.kill(data["pid"], signal.SIGTERM)
                    ELine("unittest upon server <{}:{}> failed.".format(data["app"], data["pid"]))
                    return False
                else:
                    ILine("unittest passed.")
            else:
                WLine("server <{}> is not running.".format(data["pid"]))
                return False
        else:
            WLine("no unittest case.")
        DLine("{}::_test_cases END".format(self.__class__.__name__))
        return True

    @ApiHandler.route("/get_info")
    async def _get_info(self, headers: dict, data: dict):
        return self.getInfo()

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


    def __init__(self, **kwargs):
        super().__init__(**kwargs)

