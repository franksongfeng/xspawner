# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

from xspawner.xspawner import Spawner, ApiHandler  # NOQA

class Loader(Spawner):

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

