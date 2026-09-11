#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

mkdir -p \
    /var/log/xspawner

# open xspawner service
python3 -m xspawner.service start ./xspawner.json

# execute test
# sleep 1
# python3 -m xspawner.plugins.spawner.tests https://xspawner.com:8668