#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""插件静态文件路由（/static/(.*) -> plugins/<plugin>/static/(.*)）的测试。

运行：
    python -m unittest discover -s xspawner/tests -t . -v

测试直接用与 ``XSpawner.__init__`` 相同的 root（``RES_DIR_TEMP.format(plugin)``
的绝对路径）挂载 ``ResourceHandler``，不启动数据库/systemd。
若本机缺少 pycurl/asyncpg（xspawner.xspawner 无法导入），用例自动跳过。
"""

import os
import shutil
import tempfile
import unittest

from tornado.httpclient import AsyncHTTPClient
from tornado.web import Application

from xspawner.constants import RES_DIR_TEMP

try:
    from xspawner import xspawner as xs
except Exception as exc:            # pragma: no cover - 仅影响依赖不全的开发机
    xs = None
    XS_IMPORT_ERROR = exc
else:
    XS_IMPORT_ERROR = None

SKIP_REASON = "xspawner.xspawner 无法导入: {}".format(XS_IMPORT_ERROR)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    kwargs = {} if isinstance(data, bytes) else {"newline": "\n"}
    with open(path, mode, **kwargs) as f:
        f.write(data)


class StaticRouteTests(unittest.IsolatedAsyncioTestCase):
    """通过真实 HTTP 请求验证静态文件路由。"""

    async def asyncSetUp(self):
        if xs is None:
            self.skipTest(SKIP_REASON)

        self.tmp = tempfile.mkdtemp(prefix="xs-static-")
        # 服务目录：<tmp>/xspawner/plugins/demoplugin
        self.service_dir = os.path.join(self.tmp, "xspawner", "plugins", "demoplugin")
        self.static_dir = os.path.join(self.service_dir, "static")
        os.makedirs(self.static_dir)

        _write(os.path.join(self.static_dir, "hello.txt"), "hello-static")
        _write(os.path.join(self.static_dir, "css", "app.css"), "body{color:red}\n")
        os.makedirs(os.path.join(self.static_dir, "js"))
        # 服务目录根下的文件不在 static 子目录内，不应该被暴露
        _write(os.path.join(self.service_dir, "logo.png"), b"PNG")

        # 与 XSpawner.__init__ 一致：root = 插件 static 子目录的绝对路径
        root = self.static_dir
        self.app = Application([(r"/static/(.*)", xs.ResourceHandler, {"path": root})])
        self.server = self.app.listen(0, address="127.0.0.1")
        self.port = list(self.server._sockets.values())[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        await super().asyncTearDown()

    async def fetch(self, path):
        client = AsyncHTTPClient()
        return await client.fetch(
            "http://127.0.0.1:{}{}".format(self.port, path), raise_error=False)

    async def test_static_file(self):
        resp = await self.fetch("/static/hello.txt")
        self.assertEqual(resp.code, 200)
        self.assertEqual(resp.body, b"hello-static")
        self.assertTrue(resp.headers.get("Content-Type").startswith("text/plain"))
        self.assertIn("no-cache", resp.headers.get("Cache-Control", ""))

    async def test_nested_static_file_and_content_type(self):
        resp = await self.fetch("/static/css/app.css")
        self.assertEqual(resp.code, 200)
        self.assertEqual(resp.body, b"body{color:red}\n")
        self.assertTrue(resp.headers.get("Content-Type").startswith("text/css"))

    async def test_missing_file_404(self):
        resp = await self.fetch("/static/missing.txt")
        self.assertEqual(resp.code, 404)

    async def test_service_dir_file_not_exposed(self):
        # 只暴露 <plugin>/static/，服务目录根下的文件不可见
        resp = await self.fetch("/static/logo.png")
        self.assertEqual(resp.code, 404)

    async def test_directory_403(self):
        resp = await self.fetch("/static/js")
        self.assertEqual(resp.code, 403)

    async def test_traversal_403(self):
        outside = os.path.join(self.tmp, "outside.txt")
        _write(outside, "outside")
        resp = await self.fetch("/static/%2e%2e/%2e%2e/outside.txt")
        self.assertEqual(resp.code, 403)


class StaticRootTests(unittest.TestCase):
    def test_res_dir_is_plugin_static(self):
        path = os.path.abspath(RES_DIR_TEMP.format("demoplugin"))
        self.assertTrue(
            path.endswith(os.path.join("plugins", "demoplugin", "static")), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
