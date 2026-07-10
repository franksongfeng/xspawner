# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
from xspawner.xspawner import * # NOQA
from xspawner.apps.spawner import *  # NOQA
import os.path
import datetime
import json
import random

class Evt(Spawner):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @ApiHandler.route("/")
    def _(self, headers: dict, data: dict):
        return "This is a SSE example and you just need to request to /loop"

    @FlowHandler.route("/loop")
    def _loop(self, headers: dict, data: dict):
        evt = {
            "event": "message",
            "data": str(datetime.datetime.now())
        }
        # send a event
        return evt
