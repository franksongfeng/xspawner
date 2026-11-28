#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""服务 id / 插件名校验与插件路径安全的单元测试。

运行：
    python -m unittest discover -s xspawner/tests -t . -v

不需要 systemd；方法级用例会用 mock 替换 open_service / wait_port_sync 等。
若本机缺少 pycurl（xspawner.xspawner 无法导入），方法级用例自动跳过。
"""

import io
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from xspawner.constants import PLUGIN_DIR, Config
from xspawner.validation import (
    is_valid_plugin_name,
    is_valid_port,
    is_valid_service_id,
    plugin_realpath,
)

try:
    from xspawner import xspawner as xs
except Exception as exc:            # pragma: no cover - 仅影响无 pycurl 的开发机
    xs = None
    XS_IMPORT_ERROR = exc
else:
    XS_IMPORT_ERROR = None

SKIP_REASON = "xspawner.xspawner 无法导入: {}".format(XS_IMPORT_ERROR)


class ServiceIdTests(unittest.TestCase):
    def test_accepts_normal_ids(self):
        for value in ("admin", "example-com", "10-0-0-5", "a.b", "_x", "A1"):
            self.assertTrue(is_valid_service_id(value), value)

    def test_accepts_max_length(self):
        self.assertTrue(is_valid_service_id("a" * 64))

    def test_rejects_bad_ids(self):
        for value in ("", ".", "..", "a/b", "a b", "a\tb", "管理员",
                      "a" * 65, "../etc/passwd", "a:b", None, 123, ["admin"]):
            self.assertFalse(is_valid_service_id(value), repr(value))


class PluginNameTests(unittest.TestCase):
    def test_accepts_module_names(self):
        for value in ("administrator", "foo", "_tmp", "Plugin2"):
            self.assertTrue(is_valid_plugin_name(value), value)

    def test_rejects_bad_names(self):
        for value in ("", ".", "..", ".administrator", "administrator.encryption",
                      "a-b", "a/b", "../x", "a b", "管理员", "1abc", "a" * 65,
                      None, 1):
            self.assertFalse(is_valid_plugin_name(value), repr(value))


class PortTests(unittest.TestCase):
    def test_accepts_ports(self):
        for value in (1, 8000, 65535, "8080", 8000.0):
            self.assertTrue(is_valid_port(value), value)

    def test_rejects_ports(self):
        for value in (0, -1, 65536, "0", "abc", "", None, True, 1.5, " 8000"):
            self.assertFalse(is_valid_port(value), repr(value))


class PluginRealpathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        self.plugins = os.path.join(self.tmp, PLUGIN_DIR)
        os.makedirs(os.path.join(self.plugins, "foo"))
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inside_plugins(self):
        target = plugin_realpath("foo", "foo.py")
        self.assertTrue(target.startswith(os.path.realpath(self.plugins) + os.sep))

    def test_rejects_escape(self):
        for parts in (("../foo",), ("/etc/passwd",), ("foo", "..", "..", "bar")):
            with self.assertRaises(ValueError, msg=str(parts)):
                plugin_realpath(*parts)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            plugin_realpath()

    def test_rejects_symlink_escape(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        link = os.path.join(self.plugins, "link")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):      # Windows 无权限时跳过
            self.skipTest("当前平台不允许创建符号链接")
        with self.assertRaises(ValueError):
            plugin_realpath("link", "x.py")


class _NullLogger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = critical = debug


def _make_config(**overrides):
    fields = dict(
        id="admin", plugin="administrator", host="example.com", port=8000,
        access="127.0.0.1", parent="", log=True, severity="info",
        ssl=False, certfile="", keyfile="", libs="", additional="",
    )
    fields.update(overrides)
    return Config(**fields)


def _bare_server(config=None):
    """绕过 __init__（不建 Log/HTTPServer），只给方法测试提供最小状态。"""
    srv = object.__new__(xs.Spawner)
    srv._config = config or _make_config()
    srv._logger = _NullLogger()
    return srv


@unittest.skipIf(xs is None, SKIP_REASON)
class StartChildTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = _bare_server()
        self.srv._ioloop = xs.tornado.ioloop.IOLoop.current()

    def _patch_runtime(self, open_ok=True):
        return (
            mock.patch.object(xs, "open_service", return_value=open_ok),
            mock.patch.object(xs.tornado.gen, "sleep", new=mock.AsyncMock()),
        )

    async def _start(self, data, stored=None, post_json_return=None,
                     post_json_side_effect=None, ready_return=None):
        self.srv.getValue = mock.AsyncMock(return_value=stored)
        self.srv.addValue = mock.AsyncMock(return_value=True)
        self.srv.postJson = mock.AsyncMock(
            return_value=post_json_return,
            side_effect=post_json_side_effect)
        if ready_return is None:
            child_id = data.get("id", "child") if isinstance(data, dict) else "child"
            ready_return = {"id": child_id, "pid": 321}
        self.srv._wait_http_ready = mock.AsyncMock(return_value=ready_return)
        p_open, p_sleep = self._patch_runtime()
        with p_open as open_mock, p_sleep:
            rt = await self.srv._start_child(None, data)
        return rt, open_mock

    async def test_partial_unknown_id_is_rejected(self):
        rt, open_mock = await self._start({"id": "ghost"})
        self.assertFalse(rt)
        open_mock.assert_not_called()

    async def test_invalid_id_is_rejected_before_lookup(self):
        for bad in ("../evil", "a/b", "", "管理员", "a" * 65):
            self.srv.getValue = mock.AsyncMock()
            rt, open_mock = await self._start({"id": bad})
            self.assertFalse(rt, bad)
            open_mock.assert_not_called()
            self.srv.getValue.assert_not_called()

    async def test_full_config_can_create_child(self):
        data = _make_config(id="child1", port=8101, parent="admin")._asdict()
        rt, open_mock = await self._start(data)
        self.assertEqual(rt, {"id": "child1", "pid": 321})
        open_mock.assert_called_once()
        self.assertEqual(open_mock.call_args[0][0].id, "child1")
        self.srv.addValue.assert_awaited_once()

    async def test_stored_config_can_be_started_by_id_only(self):
        stored = _make_config(id="child2", port=8102, parent="admin")._asdict()
        rt, open_mock = await self._start({"id": "child2"}, stored=stored)
        self.assertEqual(rt, {"id": "child2", "pid": 321})
        open_mock.assert_called_once()
        self.srv.addValue.assert_not_called()

    async def test_invalid_config_is_rejected(self):
        for overrides in (
            {"plugin": "administrator.encryption"},
            {"plugin": "a-b"},
            {"port": 0},
            {"port": 70000},
        ):
            fields = {"id": "child3", "port": 8103}
            fields.update(overrides)
            data = _make_config(**fields)._asdict()
            rt, open_mock = await self._start(data)
            self.assertFalse(rt, overrides)
            open_mock.assert_not_called()

    async def test_refuses_self(self):
        rt, open_mock = await self._start({"id": "admin"})
        self.assertFalse(rt)
        open_mock.assert_not_called()

    async def test_ready_response_without_pid(self):
        # /get_info 结果没有 pid 时不应崩溃，pid 返回 None
        data = _make_config(id="child4", port=8104)._asdict()
        rt, _ = await self._start(data, ready_return={"id": "child4"})
        self.assertEqual(rt, {"id": "child4", "pid": None})

    async def test_grandchildren_unreachable_is_tolerated(self):
        # 子服务 HTTP 不可达：本服务已启动成功，不能整体判失败
        data = _make_config(id="child5", port=8105)._asdict()
        rt, open_mock = await self._start(
            data, post_json_side_effect=Exception("boom"))
        self.assertIsInstance(rt, dict)
        self.assertEqual(rt["id"], "child5")
        self.assertNotIn("children_failed", rt)
        open_mock.assert_called_once()

    async def test_grandchild_failure_is_tolerated(self):
        data = _make_config(id="child6", port=8106)._asdict()
        rt, _ = await self._start(
            data, post_json_side_effect=[["gc1"], False])
        self.assertEqual(rt, {"id": "child6", "pid": 321})

    async def test_grandchildren_started(self):
        data = _make_config(id="child7", port=8107)._asdict()
        rt, _ = await self._start(
            data, post_json_side_effect=[["gc1"], {"id": "gc1"}])
        self.assertNotIn("children_failed", rt)

    async def test_waits_with_ready_timeout(self):
        data = _make_config(id="child8", port=8108)._asdict()
        self.srv.getValue = mock.AsyncMock(return_value=None)
        self.srv.addValue = mock.AsyncMock(return_value=True)
        self.srv.postJson = mock.AsyncMock(return_value=None)
        ready = mock.AsyncMock(return_value={"id": "child8", "pid": 321})
        self.srv._wait_http_ready = ready
        with mock.patch.object(xs, "open_service", return_value=True), \
                mock.patch.object(xs.tornado.gen, "sleep", new=mock.AsyncMock()):
            rt = await self.srv._start_child(None, data)
        ready.assert_awaited_once()
        self.assertEqual(
            ready.call_args.kwargs.get("timeout"), xs.SERVICE_READY_TIMEOUT)
        self.assertEqual(rt, {"id": "child8", "pid": 321})

    async def test_stop_child_rejects_invalid_id(self):
        self.srv.getValue = mock.AsyncMock()
        rt = await self.srv._stop_child(None, {"id": "../evil"})
        self.assertFalse(rt)
        self.srv.getValue.assert_not_called()


@unittest.skipIf(xs is None, SKIP_REASON)
class DeployChildValidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = _bare_server()
        self.srv._install_plugin = mock.AsyncMock(return_value=True)
        self.srv.getValues = mock.AsyncMock(return_value={})
        self.srv.addValue = mock.AsyncMock(return_value=True)
        self.srv.delValue = mock.AsyncMock(return_value=True)

    async def test_rejects_invalid_id(self):
        rt = await self.srv._deploy_child(
            None, b"x=1", "ok.py", {"id": "../evil", "port": "9000"})
        self.assertFalse(rt)
        self.srv._install_plugin.assert_not_called()

    async def test_rejects_invalid_port(self):
        rt = await self.srv._deploy_child(
            None, b"x=1", "ok.py", {"id": "child", "port": "70000"})
        self.assertFalse(rt)
        self.srv._install_plugin.assert_not_called()

    async def test_rejects_invalid_plugin_file_name(self):
        rt = await self.srv._deploy_child(
            None, b"x=1", "bad-name.py", {"id": "child", "port": "9000"})
        self.assertFalse(rt)
        self.srv._install_plugin.assert_not_called()

    async def test_cleanup_when_start_raises(self):
        self.srv._start_child = mock.AsyncMock(side_effect=Exception("boom"))
        rt = await self.srv._deploy_child(
            None, b"x=1", "ok.py", {"id": "child", "port": "9000"})
        self.assertFalse(rt)
        self.srv.delValue.assert_awaited_once_with("child")

    async def test_cleanup_when_start_returns_false(self):
        self.srv._start_child = mock.AsyncMock(return_value=False)
        rt = await self.srv._deploy_child(
            None, b"x=1", "ok.py", {"id": "child", "port": "9000"})
        self.assertFalse(rt)
        self.srv.delValue.assert_awaited_once_with("child")

    async def test_no_cleanup_on_success(self):
        self.srv._start_child = mock.AsyncMock(
            return_value={"id": "child", "pid": 1})
        rt = await self.srv._deploy_child(
            None, b"x=1", "ok.py", {"id": "child", "port": "9000"})
        self.assertTrue(rt)
        self.srv.delValue.assert_not_awaited()


@unittest.skipIf(xs is None, SKIP_REASON)
class PluginOpsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_cwd = os.getcwd()
        self.plugins = os.path.join(self.tmp, PLUGIN_DIR)
        os.makedirs(os.path.join(self.plugins, "administrator"))
        with open(os.path.join(self.plugins, "administrator", "encryption.py"), "w", newline="\n") as f:
            f.write("# admin\n")
        os.makedirs(os.path.join(self.plugins, "bar"))
        with open(os.path.join(self.plugins, "bar", "bar.py"), "w", newline="\n") as f:
            f.write("# bar\n")
        with open(os.path.join(self.plugins, "foo.py"), "w", newline="\n") as f:
            f.write("# foo\n")
        os.chdir(self.tmp)
        self.srv = _bare_server()

    async def asyncTearDown(self):
        os.chdir(self.old_cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_clean_rejects_root_names(self):
        for bad in (".", "..", ".administrator", "administrator.encryption",
                    "administrator", "spawner", "a/b"):
            rt = await self.srv._clean_plugin(None, {"plugin": bad})
            self.assertFalse(rt, bad)
        self.assertTrue(os.path.isdir(os.path.join(self.plugins, "administrator")))
        self.assertTrue(os.path.isfile(os.path.join(
            self.plugins, "administrator", "encryption.py")))

    async def test_clean_removes_plugin_package(self):
        rt = await self.srv._clean_plugin(None, {"plugin": "bar"})
        self.assertTrue(rt)
        self.assertFalse(os.path.exists(os.path.join(self.plugins, "bar")))

    async def test_clean_removes_plugin_module(self):
        rt = await self.srv._clean_plugin(None, {"plugin": "foo"})
        self.assertTrue(rt)
        self.assertFalse(os.path.exists(os.path.join(self.plugins, "foo.py")))

    async def test_download_rejects_invalid_name(self):
        rt = await self.srv._download_plugin(None, {"plugin": "."})
        self.assertFalse(rt)

    async def test_download_module(self):
        rt = await self.srv._download_plugin(None, {"plugin": "foo"})
        self.assertIsInstance(rt, tuple)
        self.assertEqual(rt[1], "foo.py")
        self.assertEqual(rt[0], b"# foo\n")

    async def test_download_package(self):
        rt = await self.srv._download_plugin(None, {"plugin": "bar"})
        self.assertIsInstance(rt, tuple)
        self.assertEqual(rt[1], "bar.zip")
        with zipfile.ZipFile(io.BytesIO(rt[0])) as zipf:
            self.assertIn("bar/bar.py", zipf.namelist())
        # 临时 zip 不应残留
        self.assertFalse(os.path.exists(os.path.join(self.plugins, "bar.zip")))

    async def test_upload_rejects_unsupported_or_bad_names(self):
        for fname in ("evil.txt", "a-b.py", "..py", ".py"):
            rt = await self.srv._upload_plugin(None, b"x=1", fname, {})
            self.assertFalse(rt, fname)

    async def test_upload_module_becomes_package(self):
        rt = await self.srv._upload_plugin(None, b"print(1)\n", "OK.PY", {})
        self.assertTrue(rt)
        self.assertTrue(os.path.isfile(
            os.path.join(self.plugins, "OK", "__init__.py")))
        self.assertFalse(os.path.exists(
            os.path.join(self.plugins, "OK.py")))

    async def test_upload_rejects_zip_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zipf:
            zipf.writestr("../evil.py", "print(1)\n")
        rt = await self.srv._upload_plugin(None, buf.getvalue(), "pkg.zip", {})
        self.assertFalse(rt)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.py")))

    async def test_upload_rejects_zip_overwriting_other_plugin(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zipf:
            zipf.writestr("administrator/administrator.py", "print('pwn')\n")
        rt = await self.srv._upload_plugin(None, buf.getvalue(), "foo.zip", {})
        self.assertFalse(rt)
        self.assertFalse(os.path.exists(os.path.join(
            self.plugins, "administrator", "administrator.py")))

    async def test_upload_accepts_matching_package_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zipf:
            zipf.writestr("pkg2/__init__.py", "# pkg2\n")
            zipf.writestr("pkg2/pkg2.py", "# pkg2\n")
        rt = await self.srv._upload_plugin(None, buf.getvalue(), "pkg2.zip", {})
        self.assertTrue(rt)
        self.assertTrue(os.path.isfile(os.path.join(self.plugins, "pkg2", "pkg2.py")))
        self.assertTrue(os.path.isfile(os.path.join(self.plugins, "pkg2", "__init__.py")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
