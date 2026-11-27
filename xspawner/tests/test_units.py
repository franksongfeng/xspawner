#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""systemd socket activation 单元生成的测试。"""

import unittest

from xspawner.constants import Config
from xspawner.service import generate_service_file, generate_socket_file


def _config(**overrides):
    fields = dict(
        id="demo", plugin="helloworld", host="localhost", port=9001,
        access="127.0.0.1", parent="admin", log=True, severity="info",
        ssl=False, certfile="", keyfile="", libs="", additional="",
    )
    fields.update(overrides)
    return Config(**fields)


class UnitGenerationTests(unittest.TestCase):
    def test_service_requires_socket(self):
        text = generate_service_file(_config())
        self.assertIn("Requires=admin.service demo.socket", text)
        self.assertIn("After=admin.service demo.socket", text)
        self.assertIn("TimeoutStopSec=30", text)

    def test_root_service_after_network(self):
        text = generate_service_file(_config(parent=""))
        self.assertIn("Requires=network.target demo.socket", text)
        self.assertIn("After=network.target demo.socket", text)

    def test_socket_listen_address(self):
        text = generate_socket_file(_config())
        self.assertIn("ListenStream=127.0.0.1:9001", text)
        self.assertIn("Service=demo.service", text)
        self.assertIn("PartOf=admin.service", text)

    def test_socket_without_parent_has_no_partof(self):
        text = generate_socket_file(_config(parent=""))
        self.assertNotIn("PartOf=", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
