# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import unittest
import requests
import re
import sys
import os
import json

def is_html(text):
    html_pattern = re.compile(r'<[^>]+>', re.IGNORECASE)
    return bool(html_pattern.search(text))

def is_json(text):
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


class Test(unittest.TestCase):
    addr: str = None
    def setUp(self):
        super().setUp()
        self.addr = os.getenv("SERVER")

    def tearDown(self):
        return super().tearDown()

    def test_addr(self):
        self.assertIsNotNone(self.addr)

    def test_(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/")
        self.assertTrue(is_html(rt.text))

    def test_get_config(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/get_config")
        self.assertTrue(is_json(rt.text))

    def test_get_state(self):
        if self.addr is None:
            raise ValueError("addr is None")
        rt = requests.get(f"{self.addr}/get_state")
        self.assertTrue(is_json(rt.text))
