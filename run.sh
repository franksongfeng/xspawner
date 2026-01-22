#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

# start spawner
nohup sudo python3 -u -m xspawner --name $1 --app $2 --host $3 --port $4 --severity debug &

# test spawner
sleep 1
sudo python3 -m xspawner.apps.spawner.tests http://$3:$4
