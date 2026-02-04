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


class AInputWait(Spawner):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @UiHandler.route("/")
    async def _(self):
        put_input('search', label='搜索框', placeholder='输入关键词...')
        
        while True:
            # ✅ 等待 pin 值发生变化
            changed = await pin_wait_change('search')
            
            # 获取变化后的值
            search_text = await pin.search
            put_text(f"搜索: {search_text}")
            
            # 模拟搜索
            if search_text:
                put_text(f"正在搜索: {search_text}...")