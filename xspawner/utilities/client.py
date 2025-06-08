# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
import tornado.httpclient
import tornado.escape
import json
import io
import os
import mimetypes
from .misc import get_file_type, make_multipart_request, parse_reply

class Client:
    def __init__(self, addr):
        self.addr = addr

    def uploadFile(self, path, filename, fargs):
        if os.path.isfile(filename):
            with open(filename, "rb") as f:
                return self.postForm(
                    path,
                    f.read(),
                    os.path.basename(filename),
                    fargs)

    def downloadFile(self, path, fargs, fdir="."):
        res = self.postJson(path, fargs)
        if res:
            fdata, fname = res
            with open(f"{fdir}/{fname}", "wb") as f:
                f.write(fdata)

    def postJson(self, path, jdata):
        headers = {
            "Content-Type": "application/json"
        }
        body = json.dumps(jdata) 
        return self.postSync(path, headers, body)


    def postForm(self, path, fdata, fname, fargs={}):
        args = {k: [str(fargs[k]).encode()] for k in fargs}
        docs = {
            "file": [
                {
                    "filename": fname,
                    "body": fdata,
                    "content_type": get_file_type(fname)
                }
            ]
        } if fdata or fname else {}
        headers, body = make_multipart_request(args, docs)
        return self.postSync(path, headers, body)


    def postSync(self, path, headers, body):
        client = tornado.httpclient.HTTPClient()
        url = self.addr + path
        try:
            res = client.fetch(url, method='POST', headers=headers, body=body)
            if res.code == 200:
                return parse_reply(res)
        except Exception as e:
            print('Exception {}:{}'.format(e.__class__.__name__, e))

