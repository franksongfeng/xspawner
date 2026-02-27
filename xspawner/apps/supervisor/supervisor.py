# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import *
from pywebio.input import *
from pywebio.output import *
from pywebio.pin import *
from pywebio.session import *
from pywebio.utils import *
from pywebio_battery import *
from xspawner.serviceable import * # NOQA
from xspawner.xspawner import * # NOQA
from xspawner.apps.spawner import * # NOQA
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
CSS = read_text_file("xspawner/apps/supervisor/common.css")


class Supervisor(Spawner): # NOQA


    @UiHandler.route("/")
    @config(theme="yeti")
    async def _(self):
        set_env(title="首页", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        tab_title = """
        <div class="markdown-table-container">
            <div class="markdown-table-title">{}</div>
        </div>
        """

        put_html(tab_title.format("信息"))
        tab_text = "| 类型 | 进程 | 时间 | 地址 |\n"
        tab_text +="| ---- | ---- | ---- | ---- |\n"
        tab_text +="| {} | {} | {} | {} |\n".format(
            self.__class__.__name__,
            self.getPid(),
            self.getPidTime(),
            self.getAddr()
            )
        put_markdown(tab_text, sanitize=False)

        put_html(tab_title.format("配置"))
        json_str = json.dumps(self.getConfig()._asdict(), indent=4, separators=(',', ':'))
        put_code(json_str, language="json")

        if self.getState():
            put_html(tab_title.format("状态"))
            json_str = json.dumps(self.getState(), cls=SrvJSONEncoder, ensure_ascii=False, indent=4)
            put_code(json_str, language="json")

        if self.getAncestry() or self.getChildren():
            put_html(tab_title.format("层次"))
            content = []
            for name, addr in self.getAncestry():
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

        put_html(tab_title.format("操作"))
        addr = self.getAddr()
        funcs = [
            {"name": "创建服务", "url": "{}/create".format(addr)},
            {"name": "销毁服务", "url": "{}/delete".format(addr)},
            {"name": "查看日志", "url": "{}/log".format(addr)},
            {"name": "调试接口", "url": "{}/debug".format(addr)}
        ]
        funcs_text = "\n".join(
            f"- [{item['name']}]({item['url']})" for item in funcs
        )
        put_markdown(funcs_text)
        return True


    @UiHandler.route("/create")
    @config(theme="yeti")
    async def _create(self):
        def check_mod_file(filename):
            if get_file_type(filename) != PYTHON_MIME_TYPE:
                return False
            mod , _ = fname.split(".")
            if "__" in mod:
                return False
            return True

        DLine("{}::_create BEG".format(self.__class__.__name__))
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
        srvseverity = data["severity"]

        if srvname:
            elm = search_list_of_dict(self.getChildren(), "name", srvname)
            if elm:
                put_error('Repeated server name {}!'.format(srvname))
                return False

        if srvport:
            if is_port_used(srvport):
                put_error('Port has been used {}!'.format(srvport))
                return False

        os.chdir(os.getcwd())

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

        # start child and get its pid
        res = self._start_child(None, {"name": srvname, "app": srvapp, "port": srvport, "severity": srvseverity})
        if not res: # res is False
            put_error("Failed to start server {}.".format(srvname))
            return False

        if "addr" in res:
            # set environment variables for unittest
            os.environ["SERVER"] = res["addr"]

            # check unittest
            if not await self._test_cases(None, {"app": srvapp, "pid": res["pid"], "port": srvport}):
                put_warning("Unittest failed!")
                return True
    
        put_success("server <{} :{}> is loaded to port {} successfully.".format(srvname, res["pid"], srvport))
        DLine("{}::_create END".format(self.__class__.__name__))
        return True


    @UiHandler.route("/delete")
    @config(theme="yeti")
    async def _delete(self):
        # # discarded onchange in the name input
        # def update_pid(name):
        #     elm = search_list_of_dict(self.getChildren(), "name", name)
        #     if elm:
        #         input_update("pid", value=elm["pid"])

        def select_server(set_value):
            def set_value_and_close_popup(v):
                set_value(v)
                close_popup()

            srv_names = [server["name"] for server in self.getChildren()]
            with popup('选择已运行的服务'):
                put_buttons(srv_names, onclick=set_value_and_close_popup, outline=True)

        DLine("{}::_delete BEG".format(self.__class__.__name__))
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
                    required=False
                )
            ]
        )

        elm = search_list_of_dict(
            self.getChildren(),
            "name",
            data["name"]
        )

        if "app" not in elm:
            put_error(f"Miss app in {elm}")
            return False

        srvapp = elm["app"]
        srvpid = elm["pid"]
        srvname = elm["name"]

        if self._stop_child(None, {"name": srvname}):
            put_success("server <{} :{}> is deleted.".format(srvname, srvpid))
            if self._clean_app(None, {"app": srvapp}):
                put_success("app {} is cleaned.".format(srvapp))

        DLine("{}::_delete END".format(self.__class__.__name__))
        return True


    @UiHandler.route("/log")
    @config(theme="yeti")
    async def _log(self):
        set_env(title="服务日志", output_animation=False)
        logfile = self.getLogFile();
        if os.path.isfile(logfile):
            filter_logs(1, logfile)
            with open(logfile, 'r') as f:
                text = f.read()
                text = "```log\n" + text + "\n```"
                put_markdown(text)
            return True


    @UiHandler.route("/debug/output")
    @config(theme="yeti")
    def _dbg_output(self):
        try:
            ILine("_debug_output BEG")
            exec(self._dbg_data["code"], globals(), locals())
            if self._dbg_data["func"] == "Eval":
                ldata = locals().copy()
                del ldata["self"]
                if ldata:
                    ILine("local vars: {}".format(str(ldata)))
                    put_markdown("***\n变量值")
                    put_code(get_first_level_json(ldata), language='json')
            ILine("_debug_output END")
            return True
        except Exception as e:
            e_str = "Exception: {}\n{}".format(str(e), traceback.format_exc())
            ELine(e_str)
            put_error("出错")
            put_code(e_str, language='text')
            return False


    @UiHandler.route("/debug")
    @config(theme="yeti")
    async def _debug(self):

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
                # run_js("setTimeout(() => { PyWebIO.reload_scope('form_scope'); }, 50)")
                if data['action'] == 'trim':
                    # 处理缩进整理
                    data['code'] = trim_code(data['code'])
                elif data['action'] == 'reset':
                    # 清空 textarea 内容
                    data['code'] = ""
                else:
                    self._dbg_data = data
                    # 打开新的URL
                    await open_url(self.getAddr() + "/debug/output")
                # 递归地重新显示表单
                await tornado.gen.sleep(0.2)
                await show_form(data)

        DLine("{}::_debug BEG".format(self.__class__.__name__))
        set_env(title="调试接口", output_animation=False)

        # 显示表单
        put_scope("form_scope")
        await show_form({'code':'put_text("Hello world!")\n','func':'UI'})
        DLine("{}::_debug END".format(self.__class__.__name__))
        return True


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


def path_to_pkg(path):
    without_extension = path.replace('.py', '')
    return without_extension.replace('/', '.')

def pkg_to_path(mod):
    path = mod.replace('.', '/')
    return f"{path}.py"

def get_loaded_mods():
    return list(sys.modules.keys())

def trim_code(code):
    if not code:
        return code
    
    lines = code.split('\n')

    while lines and not lines[0].strip():
        lines.pop(0)
    
    while lines and not lines[-1].strip():
        lines.pop()
    
    if not lines:
        return ""

    min_indent = float('inf')
    for line in lines:
        if line.strip():
            leading_spaces = len(line) - len(line.lstrip())
            min_indent = min(min_indent, leading_spaces)
    
    if min_indent == float('inf'):
        return code

    trimmed_lines = []
    for line in lines:
        if len(line) > min_indent:
            trimmed_lines.append(line[min_indent:])
        else:
            trimmed_lines.append(line.lstrip())
    
    return '\n'.join(trimmed_lines)
