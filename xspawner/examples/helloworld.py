# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.xspawner import XSpawner, Reaction # NOQA


class Helloworld(XSpawner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @Reaction.route("/")
    def _(self, headers: dict, data: dict):
        return "hello, world!"