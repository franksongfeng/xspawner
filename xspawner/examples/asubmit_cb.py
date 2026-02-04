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


class ASubmitCB(Spawner):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @UiHandler.route("/")
    async def _(self):
        put_input('email', label='邮箱')
        put_input('phone', label='手机号')
        
        async def on_submit():
            email = await pin.email
            phone = await pin.phone
            put_text(f"邮箱: {email}, 手机: {phone}")
        
        put_button("提交", onclick=on_submit)
               
        async def realtime_monitor():
            while True:
                # 必须使用 await
                current_email = await pin.email
                current_phone = await pin.phone
                
                with use_scope('monitor', clear=True):
                    put_text(f"实时监控:")
                    put_text(f"邮箱: {current_email}")
                    put_text(f"手机: {current_phone}")
                
                await tornado.gen.sleep(1)  # 每秒更新
        
        run_async(realtime_monitor())