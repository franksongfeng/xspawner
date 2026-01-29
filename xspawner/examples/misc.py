# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
from pywebio.input import *
from pywebio.output import *
from xspawner.xspawner import * # NOQA
from xspawner.utilities.log import * # NOQA
import os.path
import datetime
import json


class Misc(XSpawner):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @ApiHandler.route("/upload")
    def _upload(self, headers: dict, fdata: bytes, fname: str, fargs: dict):
        with open(fname, "wb") as f:
            f.write(fdata)
        return {"succ":True,"data":{}}

    @ApiHandler.route("/download")
    def _download(self, headers: dict, data: dict):
        fdata = b"<root>hello, world!</root>"
        fname = "a.xml"
        return (fdata, fname)

    @FlowHandler.route("/loop", 5)
    def _loop(self, headers: dict, data: dict):
        evt = {
            "event": "current_time",
            "data": str(datetime.datetime.now())
        }
        DLine(evt)
        if evt:
            # push SSE
            return evt
        else:
            # stop
            return False

    @UiHandler.route("/submit")
    def _submit(self):
        usr = input("请输入你的姓名：", type=TEXT)
        pwd = input("请输入你的口令：", type=PASSWORD)
        if len(pwd) > 3:
            put_text('口令验证通过 - %s:%s' % (usr, pwd))
            ILine("succeed in authorization!")
        else:
            put_error('口令验证没通过！')
            WLine("failed to authorize!")
        return True
