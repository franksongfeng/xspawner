# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.plugins.spawner import Spawner # NOQA
from xspawner.xspawner import ApiHandler #NOQA

class Helloworld(Spawner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @ApiHandler.route("/")
    def _(self, headers: dict, data: dict):
        return "hello, world!"