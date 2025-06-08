# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import tornado.web
import tornado.escape
import tornado.httpclient
import json
from ssl import SSLContext
from typing import Optional, Union
import os.path
from .misc import make_multipart_form, make_docs, parse_reply

def js_to_dict(js):
    # return json.loads(js) or
    return tornado.escape.json_decode(js)


def dict_to_js(dt):
    # return json.dumps(dt) or
    return tornado.escape.json_encode(dt)


# The functions such as syncReg and asyncReq are for peer to peer service
# req_dict is 1-level dict composed of string key and string value
# note: urllib.parse.urlencode() and request/response.body.decode() are opposite operations dict <=> urlstr
def syncReq(url: str, method: str, data: str = None, **kwargs):
    client = tornado.httpclient.HTTPClient()
    try:
        res = client.fetch(url, method=method, body=data, **kwargs)
        return res.body.decode()
    except Exception as e:
        print('Error in msg.syncReq:', url, repr(e))
        return None
    finally:
        client.close()


def postSyncReq(url: str, data: dict):
    rstr = syncReq(url, 'POST', json.dumps(data))
    return json.loads(rstr)


def postSyncFile(url: str, filepath: str, fileargs: dict={}):
    fn = os.path.basename(filepath)
    args = {k: [fileargs[k].encode()] for k in fileargs}
    docs = make_docs({"file":[filepath]})
    boundary = tornado.escape.utf8("----WebKitFormBoundary6ebd851a13baed30")
    body = make_multipart_form(boundary, args, docs)
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(boundary.decode()),
        "Content-Length": str(len(body))
    }
    rstr = syncReq(url, method='POST', headers=headers, data=body)
    return json.loads(rstr)


def syncTLSReq(url: str, method: str, data: str = None, cert_ctx: SSLContext = None):
    return syncReq(url, method, data, ssl_options=cert_ctx)



# asyncReq and postMSg are non-blocking and synchronous
async def asyncReq(url: str, method: str, data: str = None, **kwargs):
    async_client = tornado.httpclient.AsyncHTTPClient()
    # async_client.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")
    try:
        res = await async_client.fetch(url, method=method, body=data, **kwargs)
        return res.body.decode()
    except Exception as e:
        print('Error in msg.asyncReq:', url, repr(e))
        return None


async def asyncTLSReq(url: str,
                      method: str,
                      data: Optional[Union[str, bytes]] = None,
                      cert_ctx: SSLContext = None):
    return await asyncReq(url, method, data, ssl_options=cert_ctx)


async def postAsyncReq(url: str, data: dict):
    rstr = await asyncReq(url, 'POST', json.dumps(data))
    return json.loads(rstr)


async def postAsyncFile(url: str, filepath: str, fileargs: dict={}):
    fn = os.path.basename(filepath)
    args = {k: [fileargs[k].encode()] for k in fileargs}
    docs = make_docs({"file":[filepath]})
    boundary = tornado.escape.utf8("----WebKitFormBoundary6ebd851a13baed30")
    body = make_multipart_form(boundary, args, docs)
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(boundary.decode()),
        "Content-Length": str(len(body))
    }
    rstr = await asyncReq(url, method='POST', headers=headers, data=body)
    return json.loads(rstr)


async def postAsync(url, headers, body):
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(url, method='POST', headers=headers, body=body)
        if res.code == 200:
            return parse_reply(res)
    except Exception as e:
        print('Exception {}:{}'.format(e.__class__.__name__, e))
