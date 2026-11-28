import asyncio
import unittest
from unittest import mock

from tornado.locks import Condition
from tornado.queues import Queue

from xspawner.xspawner import ApiHandler, XSpawner


class RequestQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = mock.Mock()
        self.server._req_queue = Queue()

    def enqueue(self, path):
        result = asyncio.get_running_loop().create_future()
        condition = Condition()
        notified = condition.wait()
        self.server._req_queue.put_nowait((result, condition, path, {}, b"{}"))
        return result, notified

    async def test_cancelled_plugin_does_not_stop_next_request(self):
        async def cancelled(server, headers, data):
            raise asyncio.CancelledError()

        async def healthy(server, headers, data):
            return {"ok": True}

        with mock.patch.dict(ApiHandler.path_map, {"/cancel": cancelled, "/ok": healthy}):
            failed, first_notice = self.enqueue("/cancel")
            next_result, second_notice = self.enqueue("/ok")
            worker = asyncio.create_task(XSpawner.loop(self.server))
            try:
                await asyncio.wait_for(self.server._req_queue.join(), 2)
                self.assertIs(failed.result(), False)
                self.assertEqual(next_result.result(), {"ok": True})
                self.assertTrue(first_notice.result())
                self.assertTrue(second_notice.result())
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await worker

    async def test_cancelling_busy_consumer_cancels_plugin(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def slow(server, headers, data):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with mock.patch.dict(ApiHandler.path_map, {"/slow": slow}):
            self.enqueue("/slow")
            worker = asyncio.create_task(XSpawner.loop(self.server))
            try:
                await asyncio.wait_for(started.wait(), 2)
            finally:
                worker.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(worker, 2)
            self.assertTrue(stopped.is_set())
            await asyncio.wait_for(self.server._req_queue.join(), 2)
