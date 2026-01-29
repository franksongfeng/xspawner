# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.xspawner import * # NOQA


class Helloworld(XSpawner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @ApiHandler.route("/")
    def _(self, headers: dict, data: dict):
        return "hello, world!"