# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import *
from pywebio.input import *
from pywebio.output import *
from pywebio.pin import *
from pywebio.session import *
from pywebio.utils import *
from xspawner.xspawner import * #NOQA
from xspawner.spawner import * # NOQA
from xspawner.utilities.misc import * # NOQA
from xspawner.constants import * # NOQA
import tornado.gen
import tornado.process

import datetime
import json
from decimal import Decimal
import os
import json
import traceback
import tempfile


##############################################################################
# Constants and Variables and Classes
##############################################################################


CSS = read_text_file("xspawner/plugins/supervisor/static/common.css")


class Supervisor(Spawner): # NOQA


    # ---------------- 辅助：配置弹窗 ----------------
    async def _show_config_popup(self, srv_id):
        """
        点击"配置"列按钮时弹出该服务的 JSON 配置。
        - 主服务：直接取 self._config._asdict()
        - 子服务：通过 POST /get_config 获取
        """
        if srv_id == self._config.id:
            # 主服务：内存里已有
            data = self._config._asdict()
        else:
            child = await self.getConfig(srv_id)
            if not child:
                toast(f'服务 {srv_id} 不存在', color='error')
                return
            child_addr = self.getAddr(child.port)
            try:
                data = await self.postJson(f"{child_addr}/get_config", {})
            except Exception as e:
                self.eLog(f"get_config from {child_addr} failed: {e}")
                toast('获取配置失败', color='error')
                return
            if not isinstance(data, dict):
                toast('配置格式无效', color='error')
                return

        json_str = json.dumps(data, indent=4, ensure_ascii=False)
        with popup(f'配置 - {srv_id}', closable=True):
            put_code(json_str, language='json')


    # ---------------- 辅助：异步执行 systemctl status（协程版） ----------------
    async def _run_status_text(self, srv_id, lines: int = 20,
                               timeout: int = 5) -> str:
        """
        用 tornado.process.Subprocess 异步执行 `systemctl status <id>`，
        完全基于 Tornado 事件循环，不占用任何线程。

        为避免 systemctl 在某些环境下挂住不退出，命令前置 `timeout` 工具，
        强杀超时；并在 finally 中显式回收子进程，避免残留。
        """
        proc = tornado.process.Subprocess(
            ["timeout", str(timeout),
             "systemctl", "status", srv_id,
             "-n", str(lines), "--no-pager"],
            stdout=tornado.process.Subprocess.STREAM,
            stderr=tornado.process.Subprocess.STREAM,
        )
        try:
            # 并发读两个流，避免某一端缓冲区满导致死锁
            out_b, err_b = await tornado.gen.multi([
                proc.stdout.read_until_close(),
                proc.stderr.read_until_close(),
            ])
        finally:
            # 确保子进程被回收，避免僵尸/残留
            try:
                await proc.wait_for_exit()
            except Exception:
                pass
        out = (out_b or b"").decode("utf-8", errors="replace")
        err = (err_b or b"").decode("utf-8", errors="replace")
        # systemctl status 在服务未运行时退出码非 0，但 stdout 仍有内容
        return out or err


    # ---------------- 辅助：状态弹窗 ----------------
    async def _show_status_popup(self, srv_id):
        """点击"状态"列按钮时弹出该服务的 systemctl status 文本。"""
        try:
            text = await self._run_status_text(srv_id, lines=20)
        except Exception as e:
            self.eLog(f"get status for {srv_id} failed: {e}")
            text = f"获取状态失败: {e}"
        if not text:
            text = "(无输出)"
        with popup(f'状态 - {srv_id}', closable=True):
            put_code(text, language='text')


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

        put_html(tab_title.format("服务"))

        # ---- 收集主服务 + 子服务数据 ----
        rows = []

        # 主服务本身作为第一行
        rows.append({
            "id":   self._config.id,
            "cls":  self.getClassName(),
            "vsn":  self.getVersion(),
            "pid":  self.getPid(),
            "time": self.getPidTime(),
            "addr": self.getAddr(),
        })

        # 子服务依次追加
        children = await self.getChildren()
        for child_id in children:
            child = await self.getConfig(child_id)
            if not child:
                continue
            child_addr = self.getAddr(child.port)
            try:
                info = await self.postJson(f"{child_addr}/get_info", {})
            except Exception as e:
                self.eLog("get_info from {} failed: {}".format(child_addr, e))
                info = None
            if not isinstance(info, dict):
                info = {}
            rows.append({
                "id":   child_id,
                "cls":  info.get("class", "-"),
                "vsn":  info.get("vsn", "-"),
                "pid":  info.get("pid", "-"),
                "time": info.get("start_time", "-"),
                "addr": child_addr,
            })

        # ---- 构造表格 ----

        table = []
        # 表头
        table.append([
            put_html("<b>标识</b>"),
            put_html("<b>类型</b>"),
            put_html("<b>版本</b>"),
            put_html("<b>进程</b>"),
            put_html("<b>时间</b>"),
            put_html("<b>配置</b>"),
            put_html("<b>状态</b>"),
            put_html("<b>地址</b>"),
        ])

        for idx, r in enumerate(rows):
            srv_id = r["id"]
            addr_url = "{}/".format(r["addr"])

            # 主服务的标识后面加星
            is_main = (idx == 0)                # 第一行是主服务
            id_display = f'{_esc(srv_id)} ★' if is_main else _esc(srv_id)

            table.append([
                put_html(id_display),
                put_text(str(r["cls"])),
                put_text(str(r["vsn"])),
                put_text(str(r["pid"])),
                put_text(str(r["time"])),
                put_buttons(
                    [{"label": "查看", "value": "config"}],
                    small=True, outline=True,
                    onclick=lambda _v, s=srv_id: self._show_config_popup(s),
                ),
                put_buttons(
                    [{"label": "查看", "value": "status"}],
                    small=True, outline=True,
                    onclick=lambda _v, s=srv_id: self._show_status_popup(s),
                ),
                put_link(r["addr"], url=addr_url),
            ])

        put_table(table)

        put_html(tab_title.format("操作"))
        addr = self.getAddr()
        funcs = [
            {"name": "创建服务", "url": "{}/create".format(addr)},
            {"name": "销毁服务", "url": "{}/delete".format(addr)},
            {"name": "查看日志", "url": "{}/log".format(addr)},
            {"name": "调试接口", "url": "{}/debug".format(addr)},
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
            mod , _ = filename.rsplit(".", 1)
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
                    name="id",
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
                ),
                input(
                    label="依赖库",
                    name="libs",
                    type=TEXT,
                    placeholder="pip 包名，以逗号分割（如：requests, numpy>=1.20, flask[async]）",
                    help_text="可选。服务启动时自动安装这些 pip 库；支持版本约束与 extras",
                    required=False
                ),
                textarea(
                    label="附加信息",
                    name="additional",
                    rows=5,
                    placeholder='可选，任意字符串',
                    help_text="可选。原样透传给子服务",
                    required=False
                )
            ]
        )

        if " " in data["id"]:
            put_error('Blank space is not allowed in id')
            return

        if data["port"] < 1000 or data["port"] > 65535:
            put_error('Invalid port number <1000 or > 65535')
            return

        if not data["source"]:
            put_error('No uploaded source file')
            return
        
        fname = os.path.basename(data["source"]["filename"])    # sanitize
        fdata = data["source"]["content"]
        ftype = data["source"]["mime_type"]
        srvname = data["id"]
        srvport = data["port"]
        srvseverity = data["severity"] or "info"
        additional_raw = data.get("additional") or ""
        libs_raw = (data.get("libs") or "").strip()

        # 提前校验，与 service.split_libs 同口径
        if libs_raw:
            for pkg in split_libs(libs_raw):
                if not is_valid_pip_pkg(pkg):
                    put_error('Invalid pip package spec: {}'.format(pkg))
                    return

        if srvname:
            if srvname in await self.getChildren():
                put_error('Repeated server id {}!'.format(srvname))
                return

        if srvport:
            if is_port_used(srvport):
                put_error('Port has been used {}!'.format(srvport))
                return


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
                put_error('Invalid filename {}!'.format(pkgfname))
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


        topmod = srvcls.__module__
        self.dLog(f"topmod: {topmod}")
        if len(topmod.split(".")) <= 2: 
            put_error('Wrong mod path {}!'.format(topmod))
            return

        srvapp = topmod.split(".")[2]
        self.iLog(f"srvapp: {srvapp}")

        # start child and get its pid
        child_config = {
            "id": srvname,
            "plugin": srvapp,
            "host": self._config.host,
            "port": srvport,
            "access": self._config.access,
            "parent": self._config.id,
            "log": True,
            "severity": srvseverity,
            "ssl": self._config.ssl,
            "certfile": self._config.certfile,
            "keyfile": self._config.keyfile,
            "libs": libs_raw,
            "additional": additional_raw
        }

        res = await self._start_child(None, child_config)

        if res:
            # set environment variables for unittest
            os.environ["SERVER"] = self.getAddr(srvport)

            # check unittest
            if not await self._test_child(None, child_config):
                put_error("Unittest failed!")
                if is_port_used(srvport):
                    if await self._stop_child(None, {"id": srvname}):
                        put_warning("server {} was stopped.".format(srvname))
                        if srvapp not in ["supervisor"]:
                            if await self._clean_plugin(None, {"plugin": srvapp}):
                                put_info("Plugin {} was cleaned.".format(srvapp))
                return
            put_success("Server {} is loaded to port {} successfully.".format(srvname, srvport))
            self.iLog("{}::_create END".format(self.__class__.__name__))
        else:
            put_error("Failed to start server {}.".format(srvname))


    @UiHandler.route("/delete")
    @config(theme="yeti")
    async def _delete(self):

        children = await self.getChildren()
        def select_server(set_value):
            def set_value_and_close_popup(v):
                set_value(v)
                close_popup()
            with popup('选择已运行的服务'):
                put_buttons(children, onclick=set_value_and_close_popup, outline=True)

        self.iLog("{}::_delete BEG".format(self.__class__.__name__))
        set_env(title="服务销毁", output_animation=False)
        put_html(f'<style>{CSS}</style>')
        data = await input_group(
            "销毁服务",
            [
                input(
                    label="名称",
                    name="id",
                    type=TEXT,
                    placeholder="输入已运行的服务名称",
                    action=("现有服务", select_server),
                    required=False
                )
            ]
        )

        if not data["id"]:
            put_error("Please input a server id")
            return

        elm = await self.getConfig(data["id"])
        if not elm:
            put_error("Server '{}' not found".format(data["id"]))
            return

        srvname = elm.id
        srvaddr = self.getAddr(elm.port)

        res = await self.postJson(f"{srvaddr}/get_info", {})
        if not res or not isinstance(res, dict):
            self.eLog("Invalid response from {}/get_info: {}".format(srvaddr, res))
            put_error("Failed to reach server {}".format(srvaddr))
            return

        srvpid = res["pid"]
        srvapp = res["plugin"]
        put_info("Server <{} :{}> will be deleted.".format(srvname, srvpid))
        if await self._stop_child(None, {"id": srvname}):
            put_success("Server <{} :{}> is deleted.".format(srvname, srvpid))
            if srvapp not in ["supervisor"]:
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
            if not hasattr(self, "_dbg_data"):
                put_error("没有调试数据")
                return
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



def get_first_level_json(data, pretty=True):
    """
    返回字典第一级属性的 JSON 字符串。

    对特殊类型做降级处理：
      - datetime / date / time  → ISO 字符串
      - Decimal                 → float
      - list / tuple / dict     → 递归简化
      - 其他（函数、模块、对象）→ str(value)，失败则退化为 <类型名>

    参数:
        data:   字典数据
        pretty: 是否美化输出（缩进 2 空格）

    返回:
        JSON 字符串；若 data 非 dict，返回带 error 字段的 JSON
    """
    if not isinstance(data, dict):
        return json.dumps(
            {"error": f"input must be dict, got {type(data).__name__}"},
            indent=2 if pretty else None,
            ensure_ascii=False,
        )

    result = {str(k): _simplify(v) for k, v in data.items()}
    return json.dumps(
        result,
        indent=2 if pretty else None,
        ensure_ascii=False,
        default=str,
    )


def _simplify(value):
    """把单个值简化为可 JSON 序列化的形式。"""
    # 基本类型（含 None）原样返回
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    # 时间类型 → ISO 字符串
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()

    # Decimal → float
    if isinstance(value, Decimal):
        return float(value)

    # 容器类型：递归简化；深度异常时退化为类型名
    if isinstance(value, (list, tuple)):
        try:
            return [_simplify(v) for v in value]
        except Exception:
            return f"<{type(value).__name__} len={len(value)}>"

    if isinstance(value, dict):
        try:
            return {str(k): _simplify(v) for k, v in value.items()}
        except Exception:
            return f"<dict len={len(value)}>"

    # 其他类型（函数、模块、自定义对象等）：尝试 str
    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _esc(s):
    """HTML 转义"""
    return (str(s)
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;'))
