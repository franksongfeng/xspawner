# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import unittest
import requests
import re
import sys
import os

def is_html(text):
    html_pattern = re.compile(r'<[^>]+>', re.IGNORECASE)
    return bool(html_pattern.search(text))

class Test(unittest.TestCase):
    addr: str = None
    def setUp(self):
        super().setUp()
        self.addr = os.getenv("SERVICE")

    def tearDown(self):
        return super().tearDown()

    def test_addr(self):
        self.assertIsNotNone(self.addr)

    def test_(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/")
        self.assertTrue(is_html(rt.text))

    def test_service_create(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/service/create")
        self.assertTrue(is_html(rt.text))

    def test_service_delete(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/service/delete")
        self.assertTrue(is_html(rt.text))

    def test_service_log(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/service/log")
        self.assertTrue(is_html(rt.text))