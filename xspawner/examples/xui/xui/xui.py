# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from pywebio import config
from pywebio.input import *
from pywebio.output import *
from pywebio.session import *

from xspawner.xspawner import XSpawner, Reaction, Interaction, Contaction # NOQA
from xspawner.utilities.log import DLine, ILine, WLine, ELine, CLine # NOQA
import traceback

E_CSS = """
.error-container {
  width: 700px;
  margin: 50px auto;
  font-family: Arial, sans-serif;
}

.error-header {
  background-color: #f04124;
  color: #fff;
  padding: 15px;
  font-size: 14px;
  margin-bottom: 20px;
}

.error-content {
  background-color: #f6f8fa;
}
.error-code {
  padding: 10px 15px;
  margin: 0;
  font-family: "Consolas", "Courier New", monospace;
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: 0.3px;
  white-space: pre-wrap;
}
"""


E_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document</title>
    <style>{}</style>
</head>
<body>
  <div class="error-container">
    <div class="error-header">执行出错</div>
    <div class="error-content">
      <pre class="error-code">{}</pre>
    </div>
  </div>
</body>
</html>
"""

class Xui(XSpawner):
    _code = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def getAddr(self):
        return "{}://{}:{}".format(
            "https" if self._config.security else "http",
            self._config.host,
            self._config.port)

    @Reaction.route("/r")
    def _reaction(self, headers: dict, data: dict):
        try:
            exec(self._code, globals(), locals())
            if 'result' in locals():
                ILine("reaction result = {}".format(locals()['result']))
                return locals()['result']
            else:
                ELine("reaction miss result")
                return False
        except Exception as e:
            e_str = "Exception: {}\n{}".format(str(e), traceback.format_exc())
            ELine(e_str)
            return E_HTML.format(E_CSS, e_str)

    @Interaction.route("/i")
    @config(theme="yeti")
    def _interaction(self):
        try:
            exec(self._code, globals(), locals())
            return True
        except Exception as e:
            e_str = "Exception: {}\n{}".format(str(e), traceback.format_exc())
            ELine(e_str)
            put_error("执行出错")
            put_code(e_str, language='text')
            return False


    @Interaction.route("/")
    @config(theme="yeti")
    def _(self):

        def show_form(init_data):
            def open_url(url):
                run_js('window.open(url)', url=url)

            def select_func(func):
                input_update("url", value="{}/{}".format(self.getAddr(), "i" if func == "Interaction" else "r"))
            # 显示表单
            with use_scope("form_scope", clear=True):
                data = input_group(
                    "调试接口",
                    [
                        radio(
                            label="类型",
                            name="func",
                            options=["Interaction","Reaction"],
                            inline=True,
                            value=init_data["func"],
                            onchange=select_func,
                            required=True
                        ),
                        input(
                            label="路径",
                            name="url",
                            type=TEXT,
                            value=init_data["url"],
                            readonly=True,
                            required=False
                        ),
                        textarea(
                            label='代码',
                            name='code',
                            value=init_data["code"],
                            rows=25,
                            code={
                                'mode': "python",
                                'theme': 'eclipse'
                            }
                        )
                    ]
                )
            # 处理请求
            if data:
                self._code = data["code"]               
                # 延时后重新渲染表单区域
                run_js("setTimeout(() => { PyWebIO.reload_scope('form_scope'); }, 20)")
                # 打开新的URL
                open_url(data["url"])
                # 递归地重新显示表单
                show_form(data)

        set_env(title="服务调试", output_animation=False)
        
        # 屏蔽宽屏
        # put_html(f'<style>{CSS}</style>')
        
        # 显示表单
        put_scope("form_scope")
        show_form({'func':'Interaction','url':'{}/i'.format(self.getAddr()),'code':'put_text("Hello world")\n'})

        return True