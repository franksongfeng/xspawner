#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
import json
import sys
import uuid
import argparse
import os
import os.path
import traceback
import pkgutil
import importlib

from .utilities.log import startLogger, ILine, WLine, ELine, CLine # NOQA
from .utilities.misc import is_module_available, import_package_modules # NOQA
from .constants import *
from . import __version__
from .common import Config, State

if __name__ == '__main__':
    try:
        # parse arguments
        parser = argparse.ArgumentParser(description="Commandline arguments")
        parser.add_argument("--name", type=str, required=True, help="Server name")
        parser.add_argument("--app", type=str, default="spawner", help="Application name")
        parser.add_argument("--host", type=str, required=True, help="Host address")
        parser.add_argument("--port", type=int, required=True, help="Server port")
        parser.add_argument("--severity", type=str, default="debug", help="Log level")
        parser.add_argument("--ssl", action="store_true", help="SSL/TLS Support")
        parser.add_argument("--certfile", type=str, default=None, help="Certification")
        parser.add_argument("--keyfile", type=str, default=None, help="Private key")
        parser.add_argument("--ancestry", type=str, default=None, help="Ancestry")
        args = parser.parse_args()
        
        globals()["__version__"] = __version__

        # start logger
        log_file = LOG_FILE_TEMP.format(args.name)
        if os.path.isfile(log_file):
            os.remove(log_file)
        startLogger(args.name, log_file, args.severity) # NOQA
        ILine("logger is started {} {} {}".format(args.name, log_file, args.severity))
        from .xspawner import XSpawner # NOQA
        srv_cls = XSpawner.getChildClass("{}.{}".format(APP_PKG, args.app))
        if srv_cls:
            # start server
            if args.ssl:
                ILine("SSL/TLS Support is enabled")
            ILine("server is starting {} {} {} {}".format(args.name, args.app, args.host, args.port))
            # cmd_args = {k: getattr(args, k) if hasattr(args, k) else None for k in Config._fields}
            cmd_args = vars(args)
            srv_vsn = "undefined"
            if is_module_available(f"{APP_PKG}.{args.app}.__version__"):
                mod = importlib.import_module(f"{APP_PKG}.{args.app}.__version__")
                if hasattr(mod, "__version__"):
                    srv_vsn = mod.__version__
            srv_cls.getServer(config=Config(**cmd_args),state=State(vsn=srv_vsn),children=[]).start()
        else:
            CLine("no app {}!".format(args.app))
    except Exception as e:
        CLine(traceback.format_exc()) # NOQA
