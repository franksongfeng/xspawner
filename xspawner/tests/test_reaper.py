#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""administrator 子服务巡检（PeriodicCallback + 连续失败清理）的测试。"""

import unittest
from unittest import mock

from xspawner.constants import Config

try:
    from xspawner import xspawner as xs
    from xspawner.plugins.administrator import administrator as admin
except Exception as exc:            # pragma: no cover - 仅影响依赖不全的开发机
    xs = None
    admin = None
    XS_IMPORT_ERROR = exc
else:
    XS_IMPORT_ERROR = None

SKIP_REASON = "administrator 无法导入: {}".format(XS_IMPORT_ERROR)


class _NullLogger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = critical = debug


def _config(**overrides):
    fields = dict(
        id="admin", plugin="administrator", host="localhost", port=8000,
        access="0.0.0.0", parent="", log=True, severity="info",
        ssl=False, certfile="", keyfile="", libs="", additional="",
    )
    fields.update(overrides)
    return Config(**fields)


def _bare_server():
    srv = object.__new__(admin.Administrator)
    srv._config = _config()
    srv._logger = _NullLogger()
    srv._child_failures = {}
    srv._reaping = False
    srv._reaper = None
    return srv


@unittest.skipIf(admin is None, SKIP_REASON)
class ChildReaperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.srv = _bare_server()
        self.srv._ioloop = xs.tornado.ioloop.IOLoop.current()
        self.srv.getValues = mock.AsyncMock(return_value={})
        self.srv.postJson = mock.AsyncMock(return_value={"id": "child1"})
        self.srv.delValue = mock.AsyncMock(return_value=True)

    def _child(self, child_id="child1", **overrides):
        fields = {"id": child_id, "parent": "admin", "port": 8935}
        fields.update(overrides)
        return _config(**fields)._asdict()

    async def test_periodic_callback_started(self):
        self.srv._start_child_reaper()
        try:
            self.assertIsNotNone(self.srv._reaper)
            self.assertTrue(self.srv._reaper.is_running())
        finally:
            self.srv._reaper.stop()

    async def test_schedule_uses_add_callback(self):
        self.srv._ioloop = mock.Mock()
        self.srv._schedule_child_reap()
        self.srv._ioloop.add_callback.assert_called_once()
        called = self.srv._ioloop.add_callback.call_args[0][0]
        self.assertEqual(called.__func__, self.srv._reap_dead_children.__func__)

    async def test_dead_child_removed_after_threshold(self):
        self.srv.getValues.return_value = {"child1": self._child()}
        self.srv.postJson.side_effect = Exception("down")
        with mock.patch.object(admin, "close_service", return_value=True) as close:
            for _ in range(admin.CHILD_DEAD_THRESHOLD - 1):
                await self.srv._reap_dead_children()
            self.srv.delValue.assert_not_awaited()

            await self.srv._reap_dead_children()
            self.srv.delValue.assert_awaited_once_with("child1")
            close.assert_called_once_with("child1")

    async def test_alive_child_kept(self):
        self.srv.getValues.return_value = {"child1": self._child()}
        for _ in range(admin.CHILD_DEAD_THRESHOLD + 2):
            await self.srv._reap_dead_children()
        self.srv.delValue.assert_not_awaited()
        self.assertNotIn("child1", self.srv._child_failures)

    async def test_failure_counter_resets_after_success(self):
        self.srv.getValues.return_value = {"child1": self._child()}
        self.srv.postJson.side_effect = Exception("down")
        await self.srv._reap_dead_children()
        await self.srv._reap_dead_children()
        self.assertEqual(self.srv._child_failures.get("child1"), 2)

        self.srv.postJson.side_effect = None
        self.srv.postJson.return_value = {"id": "child1"}
        await self.srv._reap_dead_children()
        self.assertNotIn("child1", self.srv._child_failures)

    async def test_ignores_non_config_and_other_parent(self):
        self.srv.getValues.return_value = {
            "state": True,
            "other": _config(id="other", parent="someone-else")._asdict(),
        }
        await self.srv._reap_dead_children()
        self.srv.postJson.assert_not_awaited()
        self.srv.delValue.assert_not_awaited()

    async def test_child_without_port_is_removed(self):
        self.srv.getValues.return_value = {"noport": self._child("noport", port=0)}
        for _ in range(admin.CHILD_DEAD_THRESHOLD):
            await self.srv._reap_dead_children()
        self.srv.postJson.assert_not_awaited()
        self.srv.delValue.assert_awaited_once_with("noport")

    async def test_reaping_guard(self):
        # 上一次巡检未结束时，新的回调直接返回，不会重复跑
        self.srv._reaping = True
        await self.srv._reap_dead_children()
        self.srv.getValues.assert_not_awaited()
        self.assertTrue(self.srv._reaping)


if __name__ == "__main__":
    unittest.main(verbosity=2)
