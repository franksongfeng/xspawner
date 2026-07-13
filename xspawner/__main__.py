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

from . import __version__
from .constants import *
from .xspawner import Config, State
from .utilities.misc import get_similar_cls

if __name__ == '__main__':
    try:
        # parse arguments
        parser = argparse.ArgumentParser(description="Commandline arguments")
        parser.add_argument("--name", type=str, required=True, help="Instance name")
        parser.add_argument("--plugin", type=str, default="supervisor", help="Application module")
        parser.add_argument("--host", type=str, required=True, help="Host access address")
        parser.add_argument("--port", type=int, required=True, help="Listening port")
        parser.add_argument("--access", type=str, default="0.0.0.0", help="Allowable access source")
        parser.add_argument("--ancestry", type=str, default="", help="Ancestry")
        parser.add_argument("--reportup", action="store_true", help="Report Up")
        parser.add_argument("--log", action="store_true", help="Log Support")
        parser.add_argument("--severity", type=str, default="debug", help="Log level")
        parser.add_argument("--ssl", action="store_true", help="SSL/TLS Support")
        parser.add_argument("--certfile", type=str, help="Certification file")
        parser.add_argument("--keyfile", type=str, help="Private key file")
        args = parser.parse_args()
        
        globals()["__version__"] = __version__

        srv_cls = get_similar_cls("{}.{}".format(PLUGIN_PKG, args.plugin), 'Spawner', 1)
        if srv_cls:
            cmd_args = vars(args)
            srv_cls.getServer(config=Config(**cmd_args),state=State(),children=[]).start()
        else:
            print("Err: the plugin {} is not found!".format(args.plugin))
    except Exception as e:
        print(traceback.format_exc()) # NOQA
