# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.
import json
from collections import namedtuple, UserDict

Config = namedtuple('Config', ['name', 'app', 'host', 'port', 'severity', 'ancestry', 'vsn'])

class State(UserDict):
    JSON_ALLOWED_TYPES = (str, int, float, bool, type(None))

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._validate_and_update(dict(*args, **kwargs))

    def __json__(self):
        return dict(self)
    
    def __setitem__(self, key, value):
        self._validate_value(value)
        super().__setitem__(key, value)

    def update(self, *args, **kwargs):
        self._validate_and_update(dict(*args, **kwargs))

    def _validate_and_update(self, new_data):
        for key, value in new_data.items():
            self._validate_value(value)
            super().__setitem__(key, value)

    def _validate_value(self, value):
        if isinstance(value, dict):
            for v in value.values():
                self._validate_value(v)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._validate_value(item)
        elif isinstance(value, self.JSON_ALLOWED_TYPES):
            pass
        else:
            raise TypeError(
                f"{type(value)} is not JSON serializable, just allow {self.JSON_ALLOWED_TYPES}"
            )

class SrvJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Config):
            return obj._asdict()
        if isinstance(obj, State):
            return obj.__json__()
        else:
            return super().default(obj)

class Serviceable(object):

    def __init__(self, config: Config, state: State, children: list, **others):
        raise NotImplementedError

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def getPid(self):
        raise NotImplementedError

    def getAddr(self):
        raise NotImplementedError
