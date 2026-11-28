#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""服务间 HTTP 超时配置的测试。

要求：请求超时不能小于内部等待时间，否则父服务会先于子服务超时。
运行：
    python -m unittest discover -s xspawner/tests -t . -v
"""

import os
import tempfile
import unittest
from unittest import mock

try:
    from xspawner import xspawner as xs
except Exception as exc:            # pragma: no cover - 仅影响依赖不全的开发机
    xs = None
    XS_IMPORT_ERROR = exc
else:
    XS_IMPORT_ERROR = None

SKIP_REASON = "xspawner.xspawner 无法导入: {}".format(XS_IMPORT_ERROR)


def _json_response(body=b"1"):
    resp = mock.Mock()
    resp.code = 200
    resp.body = body
    resp.headers.get_list.return_value = ["application/json"]
    return resp


@unittest.skipIf(xs is None, SKIP_REASON)
class TimeoutConfigTests(unittest.TestCase):
    def test_outgoing_timeout_covers_ready_wait(self):
        self.assertGreaterEqual(xs.HTTP_REQUEST_TIMEOUT, xs.SERVICE_READY_TIMEOUT)

    def test_api_wait_covers_outgoing_timeout(self):
        self.assertGreaterEqual(xs.API_WAIT_TIMEOUT, xs.HTTP_REQUEST_TIMEOUT)

    def test_ready_timeout_is_at_least_two_minutes(self):
        self.assertGreaterEqual(xs.SERVICE_READY_TIMEOUT, 120)


class _NullLogger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = critical = debug


@unittest.skipIf(xs is None, SKIP_REASON)
class PostAsyncTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_async_passes_request_timeout(self):
        client = mock.Mock()
        client.fetch = mock.AsyncMock(return_value=_json_response())
        with mock.patch.object(
                xs.tornado.httpclient, "AsyncHTTPClient", return_value=client):
            result = await xs.postAsync("http://127.0.0.1:8000/x", {}, b"{}")
        self.assertEqual(result, 1)
        client.fetch.assert_awaited_once()
        kwargs = client.fetch.call_args.kwargs
        self.assertEqual(kwargs.get("request_timeout"), xs.HTTP_REQUEST_TIMEOUT)

    async def test_post_async_accepts_explicit_timeout(self):
        client = mock.Mock()
        client.fetch = mock.AsyncMock(return_value=_json_response())
        with mock.patch.object(
                xs.tornado.httpclient, "AsyncHTTPClient", return_value=client):
            await xs.postAsync("http://127.0.0.1:8000/x", {}, b"{}", timeout=42)
        self.assertEqual(client.fetch.call_args.kwargs.get("request_timeout"), 42)

    async def test_post_async_file_passes_request_timeout(self):
        fd, path = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        try:
            with open(path, "w", newline="\n") as f:
                f.write("print(1)\n")
            client = mock.Mock()
            client.fetch = mock.AsyncMock(
                return_value=_json_response(b'{"ok": true}'))
            with mock.patch.object(
                    xs.tornado.httpclient, "AsyncHTTPClient",
                    return_value=client):
                result = await xs.postAsyncFile("http://127.0.0.1:8000/upload", path)
            self.assertEqual(result, {"ok": True})
            kwargs = client.fetch.call_args.kwargs
            self.assertEqual(
                kwargs.get("request_timeout"), xs.HTTP_REQUEST_TIMEOUT)
        finally:
            os.unlink(path)

    async def test_post_json_passes_explicit_timeout(self):
        srv = object.__new__(xs.Spawner)
        srv._logger = _NullLogger()
        client = mock.Mock()
        client.fetch = mock.AsyncMock(return_value=_json_response())
        with mock.patch.object(
                xs.tornado.httpclient, "AsyncHTTPClient", return_value=client):
            result = await srv.postJson(
                "http://127.0.0.1:8000/get_info", {}, timeout=5)
        self.assertEqual(result, 1)
        self.assertEqual(client.fetch.call_args.kwargs.get("request_timeout"), 5)

    async def test_wait_http_ready_skips_old_pid(self):
        srv = object.__new__(xs.Spawner)
        srv._logger = _NullLogger()
        srv.postJson = mock.AsyncMock(side_effect=[{"pid": 1}, {"pid": 2}])
        with mock.patch.object(xs, "get_service_status", return_value={}) as status, \
                mock.patch.object(xs.tornado.gen, "sleep", new=mock.AsyncMock()):
            info = await srv._wait_http_ready(
                "http://127.0.0.1:8000", timeout=5, exclude_pid=1, service_id="child")
        status.assert_any_call("child")
        self.assertEqual(info.get("pid"), 2)
        self.assertEqual(srv.postJson.await_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
