# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import asyncio
import ipaddress
import json
import logging
import os
import uuid
from urllib.parse import urlparse

import tornado.escape
import tornado.httpclient

from .misc import make_multipart_form, make_docs, parse_reply

logger = logging.getLogger(__name__)


def js_to_dict(js):
    return tornado.escape.json_decode(js)


def dict_to_js(dt):
    return tornado.escape.json_encode(dt)


def _is_loopback_url(url: str) -> bool:
    """URL 是否指向本机环回地址。

    ⚠️ 安全假设：url 应由内部代码构造，不应直接透传用户输入。
    本函数仅用于决定是否关闭证书校验，不是 SSRF 防护。

    注：0.0.0.0 不在 loopback 范围（is_loopback=False），
    访问 https://0.0.0.0:port 时证书 SAN 不匹配、严格校验会失败，
    符合预期。
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    if host == "127.0.0.1":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_cert_for(url: str) -> bool:
    """内部调用（环回地址）关闭校验；外部调用严格校验。"""
    return not _is_loopback_url(url)


def _preview(body, limit: int = 200) -> bytes:
    """安全截取响应体片段，用于日志。body 可能为 None。"""
    return body[:limit] if body else b""


async def postAsyncFile(url: str, filepath: str, fileargs: dict = None):
    """POST 上传文件，返回解析后的 JSON 响应。

    参数:
      - url:      目标地址
      - filepath: 待上传文件路径
      - fileargs: 附加表单字段 {name: value}，value 会转为 str

    返回:
      - dict  成功
      - None  失败（文件不存在、非 200、JSON 解析失败等）
    """
    fileargs = fileargs or {}
    if not os.path.isfile(filepath):
        logger.error(f"postAsyncFile: file not found: {filepath}")
        return None

    args = {k: [str(fileargs[k]).encode()] for k in fileargs}
    docs = make_docs({"file": [filepath]})

    boundary = tornado.escape.utf8(
        "----XSPAWNER" + uuid.uuid4().hex)
    body = make_multipart_form(boundary, args, docs)
    headers = {
        "Content-Type": "multipart/form-data; boundary={}".format(
            boundary.decode()),
        # Content-Length 交给 Tornado 自动计算
    }

    # 使用 Tornado 全局单例（IOLoop 级别），复用连接池；
    # 依赖 xspawner 全局单 IOLoop 假设。
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(
            url, method="POST", headers=headers, body=body,
            validate_cert=_validate_cert_for(url),
        )
    except tornado.httpclient.HTTPClientError as e:
        logger.error(f"postAsyncFile {url}: HTTP {e.code}: {e.message}; "
                     f"body={_preview(e.response.body) if e.response else b''!r}")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"postAsyncFile {url}: "
                     f"{type(e).__name__}: {e}")
        return None

    if res.code != 200:
        logger.warning(f"postAsyncFile {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")
        return None

    rstr = res.body.decode("utf-8", errors="replace") if res.body else ""
    if not rstr:
        logger.warning(f"postAsyncFile {url}: empty response body")
        return None
    try:
        return json.loads(rstr)
    except json.JSONDecodeError as e:
        logger.error(f"postAsyncFile {url}: invalid JSON: {e}; "
                     f"body={_preview(res.body)!r}")
        return None


async def postAsync(url, headers, body):
    """POST 请求，返回 parse_reply 的结果。

    返回:
      - dict/其他  成功（HTTP 200）
      - None       失败（非 200、解析失败、网络异常等）
    """
    # 使用 Tornado 全局单例（IOLoop 级别），复用连接池；
    # 依赖 xspawner 全局单 IOLoop 假设。
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        res = await client.fetch(
            url, method="POST", headers=headers, body=body,
            validate_cert=_validate_cert_for(url),
            raise_error=False,        # 显式关闭：让下方 if res.code 生效
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"postAsync {url}: {type(e).__name__}: {e}")
        return None

    if res.code != 200:
        logger.warning(f"postAsync {url}: HTTP {res.code}, "
                       f"body={_preview(res.body)!r}")
        return None

    try:
        return parse_reply(res)
    except Exception as e:
        logger.error(f"postAsync {url}: parse_reply failed: "
                     f"{type(e).__name__}: {e}; "
                     f"body={_preview(res.body)!r}")
        return None