#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

# download xspawner
# sudo rm xspawner -rf
# sudo git clone https://github.com/franksongfeng/xspawner

# cd xspawner

export SERVER_IP=127.0.0.1
export SERVER_PORT=8080
export SERVER_NAME=root

# start xspawner daemon and root app
nohup sudo python3 -u -m xspawner --name $SERVER_NAME --app spawner --host $SERVER_IP --port $SERVER_PORT --severity info &

# test xspawner
sleep 1
sudo python3 -m xspawner.apps.spawner.tests http://$SERVER_IP:$SERVER_PORT
