#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

"""上传插件统一转换为同名包目录的测试。"""

import io
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import xspawner.xspawner as xs
import xspawner.validation as validation


class _NullLogger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class UploadPackageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="xspawner-upload-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        patches = [
            mock.patch.object(xs, "PLUGIN_DIR", self.tmp),
            mock.patch.object(validation, "PLUGIN_DIR", self.tmp),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.srv = object.__new__(xs.Spawner)
        self.srv._logger = _NullLogger()

    async def test_py_becomes_package_and_overwrites_old(self):
        old_module = os.path.join(self.tmp, "demo.py")
        with open(old_module, "wb") as f:
            f.write(b"old module")
        old_pkg = os.path.join(self.tmp, "demo")
        os.makedirs(old_pkg)
        with open(os.path.join(old_pkg, "old.py"), "wb") as f:
            f.write(b"old file")
        nested = os.path.join(old_pkg, "nested")
        os.makedirs(nested)
        with open(os.path.join(nested, "keep.txt"), "wb") as f:
            f.write(b"stale")

        ok = await self.srv._upload_plugin(
            None, b"class Demo: pass\n", "demo.py", {})

        self.assertTrue(ok)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "demo")))
        with open(os.path.join(self.tmp, "demo", "__init__.py"), "rb") as f:
            self.assertEqual(f.read(), b"class Demo: pass\n")
        self.assertFalse(os.path.exists(old_module))
        self.assertFalse(os.path.exists(os.path.join(old_pkg, "old.py")))
        self.assertFalse(os.path.exists(os.path.join(nested, "keep.txt")))

    async def test_zip_overwrites_package(self):
        pkg = os.path.join(self.tmp, "demo")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "old.py"), "wb") as f:
            f.write(b"old")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("demo/__init__.py", "class Demo: pass\n")
            z.writestr("demo/helper.py", "VALUE = 1\n")

        ok = await self.srv._upload_plugin(
            None, buf.getvalue(), "demo.zip", {})

        self.assertTrue(ok)
        with open(os.path.join(pkg, "__init__.py"), "r") as f:
            self.assertIn("class Demo", f.read())
        self.assertFalse(os.path.exists(os.path.join(pkg, "old.py")))

    async def test_zip_without_init_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("demo/module.py", "class Demo: pass\n")
        old = os.path.join(self.tmp, "demo.py")
        with open(old, "wb") as f:
            f.write(b"old")

        ok = await self.srv._upload_plugin(
            None, buf.getvalue(), "demo.zip", {})

        self.assertFalse(ok)
        self.assertTrue(os.path.exists(old))

    async def test_zip_broken_init_is_normalized(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("demo/__init__.py",
                       "from .missing import Manager\n")
            z.writestr("demo/impl.py",
                       "class Demo(Spawner):\n    pass\n")

        ok = await self.srv._upload_plugin(
            None, buf.getvalue(), "demo.zip", {})

        self.assertTrue(ok)
        with open(os.path.join(self.tmp, "demo", "__init__.py")) as f:
            init = f.read()
        self.assertIn("from .impl import Demo", init)

    async def test_expected_class_mismatch_restores_old(self):
        pkg = os.path.join(self.tmp, "demo")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "__init__.py"), "w") as f:
            f.write("old package\n")

        new_cls = type("NewClass", (), {})
        with mock.patch.object(
                xs, "_plugin_class_available", return_value=new_cls):
            ok = await self.srv._install_plugin(
                b"class NewClass: pass\n", "demo.py", {},
                validate=True, expected_class="OldClass")

        self.assertFalse(ok)
        with open(os.path.join(pkg, "__init__.py")) as f:
            self.assertEqual(f.read(), "old package\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
