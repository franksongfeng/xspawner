# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
from pywebio import config
from pywebio.input import input, select, radio, checkbox, input_update, input_group, file_upload, TEXT, PASSWORD, NUMBER
from pywebio.output import use_scope, put_warning, put_scope, put_code, put_html, put_column, put_tabs, put_markdown, put_link, put_text, put_error, put_success, put_buttons, popup, close_popup, put_table, put_collapse
from pywebio.session import run_js, set_env
from xspawner.serviceable import Config, State, SrvJSONEncoder # NOQA
from xspawner.server import Server, Reaction, Interaction, Contaction # NOQA
from xspawner.utilities.log import LEVELS, DLine, ILine, ELine, WLine, CLine # NOQA
from xspawner.utilities.misc import read_text_file, filter_logs, is_port_used, is_module_available, import_package_modules, get_child_cls # NOQA
from xspawner import * # NOQA
import inspect
import importlib
import unittest
import os
import os.path
import sys
import json
import traceback
import hashlib
import uuid
import tornado.gen
import zipfile
import tempfile
import shutil
import subprocess
import socket
import time
import signal
import mimetypes
import requests
from requests.exceptions import RequestException

##############################################################################
# Constants and Variables and Classes
##############################################################################
BASIC_CMD = "sudo python3 -u -m xspawner --name {} --app {} --host {} --port {} --severity {} --ancestry {}"
CSS = read_text_file("xspawner/apps/spawner/assets/common.css")

class Spawner(Server): # NOQA

    @Interaction.route("/")
    @config(theme="yeti")
    async def _(self):
        set_env(title="首页", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        tab_title = """
        <div class="markdown-table-container">
            <div class="markdown-table-title">{}</div>
        </div>
        """
        if self.getAncestry():
            put_html(tab_title.format("族谱"))
            tab_text = "| 名称 | 地址 |\n"
            tab_text +="| ---- | ---- |\n"
            for name, addr in self.getAncestry():
                tab_line = "| {} | {} |\n".format(
                    name,
                    addr
                    )
                tab_text += tab_line
            put_markdown(tab_text, sanitize=False)

        put_html(tab_title.format("服务"))
        tab_text = "| 名称 | 应用程序 | 类型 | 版本 | 进程 | 地址 |\n"
        tab_text +="| ---- | ---- | ---- | ---- | ---- | ---- |\n"
        tab_text +="| {} | {} | {} | {} | {} | {} |\n".format(
            self._config.name,
            self._config.app,
            self.__class__.__name__,
            self._config.vsn,
            self.getPid(),
            self.getAddr()
            )
        put_markdown(tab_text, sanitize=False)

        if self.getChildren():
            put_html(tab_title.format("子服务"))
            tab_text = "| 名称 | 应用程序 | 类型 | 版本 | 进程 | 地址 |\n"
            tab_text +="| ---- | ---- | ---- | ---- | ---- | ---- |\n"
            for elm in self.getChildren():
                tab_line = "| {} | {} | {} | {} | {} | {} |\n".format(
                    elm["name"],
                    elm["app"],
                    elm["cls"],
                    elm["vsn"],
                    elm["pid"],
                    elm["addr"]
                    )
                tab_text += tab_line
            put_markdown(tab_text, sanitize=False)

        put_html(tab_title.format("配置"))
        json_str = json.dumps(self._config, cls=SrvJSONEncoder, ensure_ascii=False, indent=4)
        put_code(json_str, language="json")

        put_html(tab_title.format("状态"))
        json_str = json.dumps(self._state, cls=SrvJSONEncoder, ensure_ascii=False, indent=4)
        put_code(json_str, language="json")

        put_html(tab_title.format("功能"))
        addr = self.getAddr()
        funcs = [
            {"name": "创建服务", "url": "{}/server/create".format(addr)},
            {"name": "销毁服务", "url": "{}/server/delete".format(addr)},
            {"name": "查看服务日志", "url": "{}/server/log".format(addr)}
        ]
        funcs_text = "\n".join(
            f"- [{item['name']}]({item['url']})" for item in funcs
        )
        put_markdown(funcs_text)
        return True


    @Interaction.route("/server/create")
    @config(theme="yeti")
    async def _service_create(self):
        def check_mod_file(filename):
            if get_file_type(filename) != PYTHON_MIME_TYPE:
                return False
            mod , _ = fname.split(".")
            if "__" in mod:
                return False
            if mod.lower() in ["xspawner", "spawner", "server"]:
                return False
            return True

        def update_scheme(scheme):
            if scheme == "https":
                input_update("certfile", readonly=False, placeholder="输入证书文件路径")
                input_update("keyfile", readonly=False, placeholder="输入私钥文件路径")
            else:
                input_update("certfile", readonly=True, placeholder="")
                input_update("keyfile", readonly=True, placeholder="")
        DLine("{}::_service_create BEG".format(self.__class__.__name__))
        set_env(title="服务创建", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        data = await input_group(
            "创建服务",
            [
                input(
                    label="名称",
                    name="name",
                    type=TEXT,
                    placeholder="输入一个新的服务名称(由英文字母和数字组成，不允许有空格和奇异字符)",
                    required=True
                ),
                input(
                    label="端口",
                    name="port",
                    type=NUMBER,
                    placeholder="输入一个可用的端口号(大于1000但小于65536的数字)",
                    required=True
                ),
                radio(
                    label="传输协议",
                    name="scheme",
                    options=["http","https"],
                    inline=True,
                    value="http",
                    onchange=update_scheme,
                    required=False
                ),
                input(
                    label="安全证书",
                    name="certfile",
                    type=TEXT,
                    readonly=True,
                    required=False
                ),
                input(
                    label="私钥文件",
                    name="keyfile",
                    type=TEXT,
                    readonly=True,
                    required=False
                ),
                select(
                    label="日志级别",
                    name="severity",
                    options=list(LEVELS.keys()),
                    type=TEXT,
                    required=False
                ),
                file_upload(
                    label="源代码",
                    name="source",
                    accept=[".py", ".zip"],
                    max_size="10M",
                    placeholder="选择一个源文件或压缩包 (*.py/*.zip)",
                    required=True
                )
            ]
        )

        if " " in data["name"]:
            put_error('Blank space is not allowed in name')
            return False

        if data["port"] < 1000 and data["port"] > 65535:
            put_error('Invalid port number <1000 or > 65535')
            return False

        if not data["source"]:
            put_error('No uploaded source file')
            return False
        
        fname = data["source"]["filename"]
        fdata = data["source"]["content"]
        ftype = data["source"]["mime_type"]
        srvname = data["name"]
        srvport = data["port"]
        srvscheme = data["scheme"]
        srvcertfile = data["certfile"]
        srvkeyfile = data["keyfile"]
        srvloglevel = data["severity"]

        json_children = self.getChildren()
        if srvname:
            if "children" in self._state:
                elm = search_list_of_dict(self.getChildren(), "name", srvname)
                if elm:
                    put_error('Repeated server name {}!'.format(srvname))
                    return False

        if srvport:
            if is_port_used(srvport):
                put_error('Port has been used {}!'.format(srvport))
                return False

        os.chdir(self.getWorkDir())

        srvcls = None
        pkgfname = None
        if get_file_type(fname) == ZIP_MIME_TYPE:
            pkgfname = tempfile.mktemp()+".zip"
            with open(pkgfname, "wb") as f:
                f.write(fdata)
            srvcls = search_for_service_cls_in_pkg(pkgfname)
            delete_file(pkgfname)
            if not srvcls:
                put_error('No required server class in {}!'.format(pkgfname))
                return False

        elif get_file_type(fname) == PYTHON_MIME_TYPE:
            pkgfname = f"{APP_DIR}/{fname}"
            if not check_mod_file(pkgfname):
                put_error('Invalid file name {}!'.format(pkgfname))
                return False
            with open(pkgfname, "wb") as f:
                f.write(fdata)

            srvcls = search_for_service_cls(pkgfname)

            if not srvcls:
                put_error('No required server class in {}!'.format(pkgfname))
                return False
        else:
            put_error('Invalid file type {}!'.format(fname))
            return False

        if not getattr(srvcls, "__module__"):
            put_error('No required server class in {}!'.format(pkgfname))
            return False

        topmod = srvcls.__module__
        DLine(f"topmod: {topmod}")
        if len(topmod.split(".")) <= 2: 
            put_error('Wrong mod path {}!'.format(topmod))
            return False

        srvapp = topmod.split(".")[2]
        ILine(f"srvapp: {srvapp}")

        ancestry = self.getAncestry()
        ancestry.append(
            (
                self._config.name,
                self.getAddr()
            )
        )
        srvancestry = json.dumps(ancestry, cls=SrvJSONEncoder, separators=(',', ':'))
        ILine(f"srvancestry: {srvancestry}")
        cmd = BASIC_CMD.format(srvname, srvapp, self._config.host, srvport, srvloglevel, srvancestry)
        if srvscheme == "https": 
            cmd += " --security --certfile {} --keyfile {}".format(srvcertfile, srvkeyfile)
        ILine(f"cmd: {cmd}")
        srvpid = start_background_process(cmd.split())
        if srvpid is None:
            put_error("Failed to start service <{} :{}>.".format(srvname, srvpid))
            return True
        srvaddr = "{}://{}:{}".format(srvscheme, self._config.host, srvport)
        ILine("srvpid: {}, srvaddr: {}".format(srvpid, srvaddr))

        # set environment variables
        os.environ["SERVICE"] = srvaddr

        # check unittest
        test_dir = "{}/{}/tests".format(APP_DIR, srvapp)
        ILine("test_dir: {}".format(test_dir))
        if os.path.isdir(test_dir):
            await tornado.gen.sleep(1)
            if is_port_used(srvport):
                # run unittest
                loader = unittest.TestLoader()
                suite = loader.discover(start_dir=test_dir, top_level_dir=test_dir)
                runner = unittest.TextTestRunner(failfast=True)
                result = runner.run(suite)
                ILine("unittest result {}".format(result))
                if result.errors or result.failures:
                    if is_port_used(srvport):
                        os.kill(srvpid, signal.SIGTERM)
                    put_error("unittest upon Service <{} :{}> failed.".format(srvname, srvpid))
                    ELine("unittest upon Service <{} :{}> failed.".format(srvname, srvpid))
                    return True
                else:
                    put_success("unittest upon Service <{} :{}> passed.".format(srvname, srvpid))
                    ILine("unittest passed.")
            else:
                WLine("service <{} :{}> is not running.".format(srvname, srvpid))
                put_error("service <{} :{}> is not running.".format(srvname, srvpid))
                return True
        else:
            WLine("no unittest case.")
    
        srvvsn = "undefined"
        if is_module_available(f"{SYSTEM_ID}.apps.{srvapp}.__version__"):
            mod = importlib.import_module(f"{SYSTEM_ID}.apps.{srvapp}.__version__")
            if hasattr(mod, "__version__"):
                srvvsn = mod.__version__
       
        new_srv = {"name": srvname, "app": srvapp, "cls": srvcls.__name__, "vsn": srvvsn, "pid": int(srvpid), "addr": srvaddr}
        self.addChild(new_srv)
        ILine("new_srv: {}".format(new_srv))

        put_success("Service <{} :{}> is loaded to port {} successfully.".format(srvname, srvpid, srvport))
        DLine("{}::_service_create END".format(self.__class__.__name__))
        return True


    @Interaction.route("/server/delete")
    @config(theme="yeti")
    async def _service_delete(self):
        def update_pid(name):
            if "children" in self._state:
                elm = search_list_of_dict(self.getChildren(), "name", name)
                if elm:
                    input_update("pid", value=elm["pid"])

        def update_name(pid):
            if "children" in self._state:
                elm = search_list_of_dict(self.getChildren(), "pid", pid)
                if elm:
                    input_update("name", value=elm["name"])

        def select_service(set_value):
            def set_value_and_close_popup(v):
                set_value(v)
                close_popup()

            srv_names = [server["name"] for server in self.getChildren()]
            with popup('选择已运行的服务'):
                put_buttons(srv_names, onclick=set_value_and_close_popup, outline=True)
        set_env(title="服务销毁", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        data = await input_group(
            "销毁服务",
            [
                input(
                    label="名称",
                    name="name",
                    type=TEXT,
                    placeholder="输入已运行的服务名称",
                    action=("现有服务", select_service),
                    onchange=update_pid,
                    required=False
                ),
                input(
                    label="进程",
                    name="pid",
                    type=NUMBER,
                    placeholder="输入已运行的服务进程标识符",
                    onchange=update_name,
                    required=False
                )
            ]
        )

        srvname = data["name"]
        srvpid = data["pid"]

        if not srvname and not srvpid:
            put_error('No selected server')
            return False

        valid_srv = False
        srvapp = None
        if srvpid:
            elm = search_list_of_dict(self.getChildren(), "pid", srvpid)
            if elm:
                srvapp = elm["app"]
                valid_srv = True
        if srvname:
            elm = search_list_of_dict(self.getChildren(), "name", srvname)
            if elm:
                srvpid = int(elm["pid"])
                srvapp = elm["app"]
                valid_srv = True
        if valid_srv:
            self.delChild(elm)
            try:
                os.kill(srvpid, signal.SIGTERM)
            except Exception as e:
                CLine(traceback.format_exc()) # NOQA
                put_warning("service <{} :{}> dont exist.".format(srvname, srvpid))
                return True
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
                    ILine(f"file {modfile} dont exist")
            put_success("Service <{} :{}> is deleted.".format(srvname, srvpid))
            return True

        put_error("Failed to find server <{} :{}>.".format(srvname, srvpid))
        return False


    @Interaction.route("/server/log")
    @config(theme="yeti")
    async def _server_log(self):
        set_env(title="服务日志", output_animation=False)
        logfile = self.getLogFile();
        if os.path.isfile(logfile):
            filter_logs(1, logfile)
            with open(logfile, 'r') as f:
                text = f.read()
                text = "```log\n" + text + "\n```"
                put_markdown(text)
            return True

    @Reaction.route("/state/get")
    def _state_get(self, headers: dict, data: dict):
        return dict(self._state)


    @Reaction.route("/state/set")
    def _state_set(self, headers: dict, data: dict):
        self._state.update(data)
        return True

    @Reaction.route("/children/get")
    def _children_get(self, headers: dict, data: dict):
        return self.getChildren()


    def getLogFile(self):
        return LOG_FILE_TEMP.format(self._config.name)

    def getAddr(self):
        return "{}://{}:{}".format(
            self.getScheme(),
            self._config.host,
            self._config.port)

    def getPid(self):
        return os.getpid()


    def getWorkDir(self):
        return os.getcwd()

    def getClassName(self):
        return self.__class__.__name__

    def getScheme(self):
        return "https" if self._config.security else "http"

    def getChildren(self):
        return self._state.get("children", [])

    def addChild(self, child):
        if "children" not in self._state:
            self._state["children"] = [child]
        else:
            self.getChildren().append(child)

    def delChild(self, child):
        self.getChildren().remove(child)


    def getAncestry(self):
        if not self._config.ancestry:
            return []
        if isinstance(self._config.ancestry, str):
            return list(json.loads(self._config.ancestry))


    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def search_list_of_dict(l, k, v):
    for e in l:
        if k in e and e[k] == v:
            return e


def search_for_service_cls(fpath):
    ILine("search_for_service_cls BEG {}".format(fpath))
    if get_file_type(fpath) == PYTHON_MIME_TYPE:
        mod_name = path_to_pkg(fpath)
        srv_cls = Server.getChildClass(mod_name)
        if srv_cls:
            ILine("srv_cls: {}".format(srv_cls))
            ILine("search_for_service_cls END {}".format(srv_cls))
            return srv_cls
    ILine("search_for_service_cls END {}".format(None))

def delete_file(file):
    if os.path.isfile(file):
        os.remove(file)

def delete_dir(dir):
    if os.path.exists(dir):
        shutil.rmtree(dir)

def delete_entries(entries):
    for entry in entries:
        if os.path.isfile(entry):
            delete_file(entry)
        else:
            delete_dir(entry)

def search_for_service_cls_in_pkg(fpath):
    ILine("search_for_service_cls_in_pkg BEG {}".format(fpath))
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
                    srv_cls = Server.getChildClass(f"{APP_PKG}.{pkg_name}")
                    if srv_cls:
                        ILine("search_for_service_cls_in_pkg END {}".format(srv_cls))
                        return srv_cls
    ILine("search_for_service_cls_in_pkg END {}".format(None))

def start_background_process(command):
    proc = subprocess.Popen(
        command,
        shell=False
    )
    time.sleep(0.5) # need a delay
    child_pid = os.popen(f"pgrep -P {proc.pid}").read().strip()
    if child_pid:
        child_pid = int(child_pid)
        return child_pid


def get_public_ip(timeout=5):
    try:
        response = requests.get("https://icanhazip.com", timeout=timeout)
        if response.status_code == 200:
            ip = response.text.strip()
            if len(ip) >= 7 and '.' in ip or ':' in ip:
                return ip
    except RequestException:
        return None

def get_file_type(filename):
    ftype = mimetypes.guess_type(filename)[0]
    return ftype if isinstance(ftype, str) else ""

def path_to_pkg(path):
    without_extension = path.replace('.py', '')
    return without_extension.replace('/', '.')

def pkg_to_path(mod):
    path = mod.replace('.', '/')
    return f"{path}.py"

def get_loaded_mods():
    return list(sys.modules.keys())
