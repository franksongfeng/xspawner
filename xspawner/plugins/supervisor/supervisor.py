# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import *
from pywebio.input import *
from pywebio.output import *
from pywebio.pin import *
from pywebio.session import *
from pywebio.utils import *
from xspawner.xspawner import * #NOQA
from xspawner.plugins.spawner import * # NOQA
from xspawner.utilities.misc import * # NOQA
from xspawner.constants import * # NOQA
from xspawner.xspawner import ApiHandler, UiHandler # NOQA
import tornado.gen
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
CSS = read_text_file("xspawner/plugins/supervisor/static/common.css")


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
        tab_text = "| 类型 | 版本 | 进程 | 时间 | 地址 |\n"
        tab_text +="| ---- | ---- | ---- | ---- | ---- |\n"
        tab_text +="| {} | {} | {} | {} | {} |\n".format(
            self.getClassName(),
            self.getVersion(),
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

        if self.getChildren():
            put_html(tab_title.format("服务"))
            content = []
            for elm in self.getChildren():
                content.append(put_link(elm["name"], url="{}/".format(elm["addr"])))
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


    @UiHandler.route("/create")
    @config(theme="yeti")
    async def _create(self):
        def check_mod_file(filename):
            if get_file_type(filename) != "text/x-python":
                return False
            mod , _ = fname.split(".")
            if "__" in mod:
                return False
            return True

        self.iLog("{}::_create BEG".format(self.__class__.__name__))
        set_env(title="服务创建", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        data = await input_group(
            "创建服务",
            [
                input(
                    label="名称",
                    name="name",
                    type=TEXT,
                    placeholder="输入一个新的服务名称(以英文字母开头，由英文字母和数字组成，不允许包含空格或奇异字符)",
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
                    label="插件源码",
                    name="source",
                    accept=[".py", ".zip"],
                    multiple=False,
                    max_size="10M",
                    placeholder="选择一个Python源文件或包 (*.py/*.zip)",
                    help_text="支持拖拽文件到此区域",
                    required=True
                )
            ]
        )

        if " " in data["name"]:
            put_error('Blank space is not allowed in name')
            return

        if data["port"] < 1000 and data["port"] > 65535:
            put_error('Invalid port number <1000 or > 65535')
            return

        if not data["source"]:
            put_error('No uploaded source file')
            return
        
        fname = data["source"]["filename"]
        fdata = data["source"]["content"]
        ftype = data["source"]["mime_type"]
        srvname = data["name"]
        srvport = data["port"]
        srvseverity = data["severity"]

        if srvname:
            elm = self.getChild(srvname)
            if elm:
                put_error('Repeated server name {}!'.format(srvname))
                return

        if srvport:
            if is_port_used(srvport):
                put_error('Port has been used {}!'.format(srvport))
                return

        os.chdir(os.getcwd())

        srvcls = None
        pkgfname = None
        if get_file_type(fname) == "application/zip":
            pkgfname = tempfile.mktemp()+".zip"
            with open(pkgfname, "wb") as f:
                f.write(fdata)
            srvcls = search_for_class_in_package(pkgfname, "Spawner")
            delete_file(pkgfname)
            if not srvcls:
                put_error('No required server class in package {}!'.format(pkgfname))
                return

        elif get_file_type(fname) == "text/x-python":
            pkgfname = f"{PLUGIN_DIR}/{fname}"
            if not check_mod_file(pkgfname):
                put_error('Invalid file name {}!'.format(pkgfname))
                return
            with open(pkgfname, "wb") as f:
                f.write(fdata)

            srvcls = search_for_class_in_file(pkgfname, "Spawner")

            if not srvcls:
                put_error('No required server class in {}!'.format(pkgfname))
                return
        else:
            put_error('Invalid file type {}!'.format(fname))
            return

        if not getattr(srvcls, "__module__"):
            put_error('No required server module in {}!'.format(pkgfname))
            return

        topmod = srvcls.__module__
        self.dLog(f"topmod: {topmod}")
        if len(topmod.split(".")) <= 2: 
            put_error('Wrong mod path {}!'.format(topmod))
            return

        srvapp = topmod.split(".")[2]
        self.iLog(f"srvapp: {srvapp}")

        # start child and get its pid
        res = await self._start_child(None, {"name": srvname, "plugin": srvapp, "port": srvport, "severity": srvseverity})
        if not res: # res is False
            put_error("Failed to start server {}.".format(srvname))
            return

        if "addr" in res:
            # set environment variables for unittest
            os.environ["SERVER"] = res["addr"]

            # check unittest
            if not await self._test_child(None, {"name": srvname, "plugin": srvapp, "port": srvport}):
                put_error("Unittest failed!")
                if is_port_used(srvport):
                    if await self._stop_child(None, {"name": srvname}):
                        put_warning("server {} was stopped.".format(srvname))
                        if srvapp not in ["spawner", "supervisor"]:
                            if await self._clean_plugin(None, {"plugin": srvapp}):
                                put_info("Plugin {} was cleaned.".format(srvapp))
                return

        put_success("Server <{} :{}> is loaded to port {} successfully.".format(srvname, res["pid"], srvport))
        self.iLog("{}::_create END".format(self.__class__.__name__))


    @UiHandler.route("/delete")
    @config(theme="yeti")
    async def _delete(self):
        # # discarded onchange in the name input
        # def update_pid(name):
        #     elm = self.getChild(name)
        #     if elm:
        #         input_update("pid", value=elm["pid"])

        def select_server(set_value):
            def set_value_and_close_popup(v):
                set_value(v)
                close_popup()

            srv_names = [server["name"] for server in self.getChildren()]
            with popup('选择已运行的服务'):
                put_buttons(srv_names, onclick=set_value_and_close_popup, outline=True)

        self.iLog("{}::_delete BEG".format(self.__class__.__name__))
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

        elm = self.getChild(data["name"])
        srvname = elm["name"]
        srvaddr = elm["addr"]

        res = await self.postJson(f"{srvaddr}/get_info", {})
        if res is None:
            self.eLog("Exception when postJson to {}/get_info".format(srvaddr))
            put_error("Exception when postJson to {}/get_info".format(srvaddr))
            return

        srvpid = res["pid"]
        srvapp = res["plugin"]
        put_info("Server <{} :{}> will be deleted.".format(srvname, srvpid))
        if await self._stop_child(None, {"name": srvname}):
            put_success("Server <{} :{}> is deleted.".format(srvname, srvpid))
            if srvapp not in ["spawner", "supervisor"]:
                if await self._clean_plugin(None, {"plugin": srvapp}):
                    put_success("Plugin {} is cleaned.".format(srvapp))
                    self.iLog("Plugin {} is cleaned.".format(srvapp))

        self.iLog("{}::_delete END".format(self.__class__.__name__))


    @UiHandler.route("/log")
    @config(theme="yeti")
    async def _log(self):
        set_env(title="服务日志", output_animation=False)
        logfile = self.getLogFile();
        if os.path.isfile(logfile):
            filter_logs(logfile, 1) # preserve 1 hour
            with open(logfile, 'r') as f:
                text = f.read()
                text = "```log\n" + text + "\n```"
                put_markdown(text)


    @UiHandler.route("/debug/output")
    @config(theme="yeti")
    def _dbg_output(self):
        try:
            self.iLog("_debug_output BEG")
            exec(self._dbg_data["code"], globals(), locals())
            if self._dbg_data["func"] == "Eval":
                ldata = locals().copy()
                del ldata["self"]
                if ldata:
                    self.iLog("local vars: {}".format(str(ldata)))
                    put_markdown("***\n变量值")
                    put_code(get_first_level_json(ldata), language='json')
            self.iLog("_debug_output END")

        except Exception as e:
            e_str = "Exception: {}\n{}".format(str(e), traceback.format_exc())
            self.eLog(e_str)
            put_error("出错")
            put_code(e_str, language='text')


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

        self.dLog("{}::_debug BEG".format(self.__class__.__name__))
        set_env(title="调试接口", output_animation=False)

        # 显示表单
        put_scope("form_scope")
        await show_form({'code':'put_text("Hello world!")\n','func':'UI'})
        self.dLog("{}::_debug END".format(self.__class__.__name__))


    def __init__(self, **kwargs):
        super().__init__(**kwargs)

