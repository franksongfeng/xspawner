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
from requests.exceptions import *
import psutil


class ASubmit(Spawner):
    name = ""
    age = ""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @UiHandler.route("/")
    async def _(self):
        async def update_display():
            while True:
                # 获取当前 pin 值
                self.name = await pin.name
                self.age = await pin.age
                
                # 更新显示
                with use_scope('output', clear=True):
                    if self.name:
                        put_text(f"姓名: {self.name}")
                    if self.age:
                        put_text(f"年龄: {self.age}")
                        if int(self.age or 0) >= 18:
                            put_success("已成年")
                        else:
                            put_warning("未成年")
                
                await tornado.gen.sleep(0.5)  # 更新频率

        # 启动实时更新
        put_input('name', label='用户名')
        put_input('age', label='年龄')
        run_async(update_display())

        # 等待提交按钮
        put_button("提交", onclick=lambda: 
            put_text("最终提交: 姓名={}, 年龄={}".format(self.name, self.age)))
