# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import config
from pywebio.input import *
from pywebio.output import *
from pywebio.session import *
from xspawner.serviceable import * # NOQA
from xspawner.xspawner import * # NOQA
from xspawner.utilities.log import * # NOQA
from xspawner.utilities.misc import * # NOQA
from xspawner import * # NOQA
import tornado.gen
import requests
from requests.exceptions import RequestException
import psutil

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
import zipfile
import tempfile
import shutil
import subprocess
import socket
import time
import signal
import mimetypes
import datetime

from .fmt_dict import get_first_level_json

##############################################################################
# Constants and Variables and Classes
##############################################################################
BASIC_CMD = "sudo python3 -u -m xspawner --name {} --app {} --host {} --port {} --severity {} --ancestry {}"
CSS = read_text_file("xspawner/apps/spawner/common.css")


class Spawner(XSpawner): # NOQA

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

        put_html(tab_title.format("基本信息"))
        tab_text = "| 名称 | 应用 | 类型 | 版本 | 进程 | 时间 | 地址 |\n"
        tab_text +="| ---- | ---- | ---- | ---- | ---- | ---- | ---- |\n"
        tab_text +="| {} | {} | {} | {} | {} | {} | {} |\n".format(
            self.getConfig().name,
            self.getConfig().app,
            self.__class__.__name__,
            self.getConfig().vsn,
            self.getPid(),
            self.getPidTime(),
            self.getAddr()
            )
        put_markdown(tab_text, sanitize=False)

        put_html(tab_title.format("启动配置"))
        json_str = json.dumps(self.getConfig()._asdict(), indent=4, separators=(',', ':'))
        put_code(json_str, language="json")

        if self.getState():
            put_html(tab_title.format("状态"))
            json_str = json.dumps(self.getState(), cls=SrvJSONEncoder, ensure_ascii=False, indent=4)
            put_code(json_str, language="json")

        put_html(tab_title.format("拓扑结构"))
        content = []
        for name, addr in self.getAncestry():
            ILine(name)
            ILine(addr)
            content.append(put_link(name, url=addr))
            content.append(put_text('>'))
        content.append(put_text(self.getConfig().name))
        if self.getChildren():
            content.append(put_text('>'))
            content.append(put_link(self.getChildren()[0]["name"], url=self.getChildren()[0]["addr"]))
            for elm in self.getChildren()[1:]:
                content.append(put_text('|'))
                content.append(put_link(elm["name"], url=elm["addr"]))
        put_row(content)

        put_html(tab_title.format("管理功能"))
        addr = self.getAddr()
        funcs = [
            {"name": "创建服务", "url": "{}/server/create".format(addr)},
            {"name": "销毁服务", "url": "{}/server/delete".format(addr)},
            {"name": "查看日志", "url": "{}/server/log".format(addr)},
            {"name": "调试接口", "url": "{}/dbg".format(addr)}
        ]
        funcs_text = "\n".join(
            f"- [{item['name']}]({item['url']})" for item in funcs
        )
        put_markdown(funcs_text)
        return True


    @Interaction.route("/server/create")
    @config(theme="yeti")
    async def _server_create(self):
        def check_mod_file(filename):
            if get_file_type(filename) != PYTHON_MIME_TYPE:
                return False
            mod , _ = fname.split(".")
            if "__" in mod:
                return False
            if mod.lower() in ["xspawner", "spawner"]:
                return False
            return True

        DLine("{}::_server_create BEG".format(self.__class__.__name__))
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
                    multiple=False,
                    max_size="10M",
                    placeholder="选择一个源文件或包 (*.py/*.zip)",
                    help_text="支持拖拽文件到此区域",
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
        srvloglevel = data["severity"]

        if srvname:
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
            srvcls = search_for_server_cls_in_pkg(pkgfname)
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

            srvcls = search_for_server_cls(pkgfname)

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
                self.getConfig().name,
                self.getAddr()
            )
        )
        srvancestry = json.dumps(ancestry, cls=SrvJSONEncoder, separators=(',', ':'))
        ILine(f"srvancestry: {srvancestry}")
        cmd = BASIC_CMD.format(srvname, srvapp, self.getConfig().host, srvport, srvloglevel, srvancestry)

        ILine(f"cmd: {cmd}")
        srvpid = start_background_process(cmd.split())
        if srvpid is None:
            put_error("Failed to start server <{} :{}>.".format(srvname, srvpid))
            return True
        srvaddr = "http://{}:{}".format(self.getConfig().host, srvport)
        ILine("srvpid: {}, srvaddr: {}".format(srvpid, srvaddr))

        # set environment variables
        os.environ["SERVER"] = srvaddr

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
                    put_error("unittest upon server <{} :{}> failed.".format(srvname, srvpid))
                    ELine("unittest upon server <{} :{}> failed.".format(srvname, srvpid))
                    return True
                else:
                    put_success("unittest upon server <{} :{}> passed.".format(srvname, srvpid))
                    ILine("unittest passed.")
            else:
                WLine("server <{} :{}> is not running.".format(srvname, srvpid))
                put_error("server <{} :{}> is not running.".format(srvname, srvpid))
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

        put_success("server <{} :{}> is loaded to port {} successfully.".format(srvname, srvpid, srvport))
        DLine("{}::_server_create END".format(self.__class__.__name__))
        return True


    @Interaction.route("/server/delete")
    @config(theme="yeti")
    async def _server_delete(self):
        def update_pid(name):
            elm = search_list_of_dict(self.getChildren(), "name", name)
            if elm:
                input_update("pid", value=elm["pid"])

        def update_name(pid):
            elm = search_list_of_dict(self.getChildren(), "pid", pid)
            if elm:
                input_update("name", value=elm["name"])

        def select_server(set_value):
            def set_value_and_close_popup(v):
                set_value(v)
                close_popup()

            srv_names = [server["name"] for server in self.getChildren()]
            with popup('选择已运行的服务'):
                put_buttons(srv_names, onclick=set_value_and_close_popup, outline=True)
        DLine("{}::_server_delete BEG".format(self.__class__.__name__))
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
                    action=("现有服务", select_server),
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
                put_warning("server <{} :{}> dont exist.".format(srvname, srvpid))
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
            put_success("server <{} :{}> is deleted.".format(srvname, srvpid))
            DLine("{}::_server_delete END".format(self.__class__.__name__))
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


    @Interaction.route("/dbg/output")
    @config(theme="yeti")
    def _dbg_output(self):
        try:
            ILine("_dbg_output BEG")
            exec(self._dbg_data["code"], globals(), locals())
            if self._dbg_data["func"] == "Eval":
                ldata = locals().copy()
                del ldata["self"]
                if ldata:
                    ILine("local vars: {}".format(str(ldata)))
                    put_markdown("***\n变量值")
                    put_code(get_first_level_json(ldata), language='json')
            ILine("_dbg_output END")
            return True
        except Exception as e:
            e_str = "Exception: {}\n{}".format(str(e), traceback.format_exc())
            ELine(e_str)
            put_error("出错")
            put_code(e_str, language='text')
            return False


    @Interaction.route("/dbg")
    @config(theme="yeti")
    async def _dbg(self):

        async def show_form(init_data):
            async def open_url(url):
                run_js('window.open(url)', url=url)

            # 显示表单
            with use_scope("form_scope", clear=True):
                data = await input_group(
                    "调试接口",
                    [
                        radio(
                            label="类型",
                            name="func",
                            options=["UI","Eval"],
                            inline=True,
                            value=init_data["func"],
                            required=True
                        ),
                        textarea(
                            label='代码',
                            name='code',
                            value=init_data["code"],
                            help_text="支持拖拽文件到此区域",
                            rows=25,
                            code={
                                "mode": "python",
                                "theme": "idea", # eclipse, idea, ssms
                                "lineNumbers": True,
                                "indentUnit": 4
                            }
                        ),
                        actions(
                            name='action',
                            buttons=[
                                {'label': '提交', 'value': 'submit', 'color': 'primary'},
                                {'label': '修剪', 'value': 'trim', 'color': 'secondary'},
                                {'label': '重置', 'value': 'reset', 'color': 'warning'}
                            ]
                        )
                    ]
                )
            # 处理请求
            if data:
                # 延时后重新渲染表单区域
                run_js("setTimeout(() => { PyWebIO.reload_scope('form_scope'); }, 50)")
                if data['action'] == 'trim':
                    # 处理缩进整理
                    data['code'] = trim_code(data['code'])
                elif data['action'] == 'reset':
                    # 清空 textarea 内容
                    data['code'] = ""
                else:
                    self._dbg_data = data
                    # 打开新的URL
                    await open_url(self.getAddr() + "/dbg/output")
                # 递归地重新显示表单
                await tornado.gen.sleep(0.2)
                await show_form(data)

        ILine("_dbg BEG")
        set_env(title="调试接口", output_animation=False)

        # 显示表单
        put_scope("form_scope")
        await show_form({'code':'put_text("Hello world!")\n','func':'UI'})
        ILine("_dbg END")
        return True


    @Reaction.route("/state/get")
    def _state_get(self, headers: dict, data: dict):
        return dict(self.getState())


    @Reaction.route("/state/set")
    def _state_set(self, headers: dict, data: dict):
        self.setState(data)
        return True

    @Reaction.route("/children/get")
    def _children_get(self, headers: dict, data: dict):
        return self.getChildren()


    def getLogFile(self):
        return LOG_FILE_TEMP.format(self.getConfig().name)

    def getAddr(self):
        return "http://{}:{}".format(
            self.getConfig().host,
            self.getConfig().port)

    def getPid(self):
        return os.getpid()

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

    def getWorkDir(self):
        return os.getcwd()

    def getClassName(self):
        return self.__class__.__name__

    def getConfig(self):
        return self._config

    def getState(self):
        return self._state

    def setState(self, data):
        return self._state.update(data)

    def getChildren(self):
        return self._children

    def addChild(self, child):
        self.getChildren().append(child)

    def delChild(self, child):
        self.getChildren().remove(child)


    def getAncestry(self):
        if not self.getConfig().ancestry:
            return []
        if isinstance(self.getConfig().ancestry, str):
            return list(json.loads(self.getConfig().ancestry))


    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def search_list_of_dict(l, k, v):
    for e in l:
        if k in e and e[k] == v:
            return e


def search_for_server_cls(fpath):
    ILine("search_for_server_cls BEG {}".format(fpath))
    if get_file_type(fpath) == PYTHON_MIME_TYPE:
        mod_name = path_to_pkg(fpath)
        srv_cls = XSpawner.getChildClass(mod_name)
        if srv_cls:
            ILine("srv_cls: {}".format(srv_cls))
            ILine("search_for_server_cls END {}".format(srv_cls))
            return srv_cls
    ILine("search_for_server_cls END {}".format(None))

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

def search_for_server_cls_in_pkg(fpath):
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
                    srv_cls = XSpawner.getChildClass(f"{APP_PKG}.{pkg_name}")
                    if srv_cls:
                        ILine("search_for_server_cls_in_pkg END {}".format(srv_cls))
                        return srv_cls
    ILine("search_for_server_cls_in_pkg END {}".format(None))

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

def trim_code(code):
    """去除代码左侧公共的缩进"""
    if not code:
        return code
    
    lines = code.split('\n')

    # 清除头部空行
    while lines and not lines[0].strip():
        lines.pop(0)
    
    # 清除尾部空行
    while lines and not lines[-1].strip():
        lines.pop()
    
    if not lines:  # 如果所有行都是空行
        return ""

    # 找到非空行的最小缩进
    min_indent = float('inf')
    for line in lines:
        if line.strip():  # 忽略空行
            leading_spaces = len(line) - len(line.lstrip())
            min_indent = min(min_indent, leading_spaces)
    
    if min_indent == float('inf'):  # 全是空行
        return code

    # 移除每行的最小缩进
    trimmed_lines = []
    for line in lines:
        if len(line) > min_indent:
            trimmed_lines.append(line[min_indent:])
        else:
            trimmed_lines.append(line.lstrip())
    
    return '\n'.join(trimmed_lines)
