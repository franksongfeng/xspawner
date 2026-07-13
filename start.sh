#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

mkdir -p \
    /var/log/xspawner

# open xspawner service
python3 -m xspawner.service open ./xspawner.json

# execute test
python3 -m xspawner.apps.spawner.tests https://xspawner.com:8668