import threading
import unittest
from unittest import mock

from tornado.ioloop import IOLoop

from xspawner import orm


class DropDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_runs_during_backup_before_drop(self):
        loop = IOLoop.current()
        loop_responded = threading.Event()
        steps = []

        def backup(conn):
            loop.add_callback(loop_responded.set)
            self.assertTrue(loop_responded.wait(timeout=3))
            steps.append("backed up")
            return True

        async def close():
            steps.append("closed")

        with mock.patch.object(orm, "backup_database", side_effect=backup), \
                mock.patch.object(orm, "close_database", side_effect=close), \
                mock.patch.object(orm, "_drop_sqlite_database",
                                  side_effect=lambda file: steps.append("deleted")):
            await orm.drop_database("sqlite://test.db")
        self.assertEqual(steps, ["backed up", "closed", "deleted"])

    async def test_backup_error_aborts_drop(self):
        with mock.patch.object(orm, "backup_database", side_effect=RuntimeError("backup failed")), \
                mock.patch.object(orm, "close_database") as close, \
                mock.patch.object(orm, "_drop_sqlite_database") as drop:
            with self.assertRaisesRegex(RuntimeError, "backup failed"):
                await orm.drop_database("sqlite://test.db")
        close.assert_not_awaited()
        drop.assert_not_called()
