#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

# download xspawner
# sudo rm xspawner -rf
# sudo git clone https://github.com/franksongfeng/xspawner

# cd xspawner

if [ -z "$1" ]; then
  export SERVER_IP=127.0.0.1
else
  export SERVER_IP=$1
fi

if [ -z "$2" ];  then
  export SERVER_PORT=8080
else
  export SERVER_PORT=$2
fi

export SERVER_NAME=root

# start spawner
nohup sudo python3 -u -m xspawner --name $SERVER_NAME --app spawner --host $SERVER_IP --port $SERVER_PORT --severity info &

# test spawner
sleep 1
sudo python3 -m xspawner.apps.spawner.tests http://$SERVER_IP:$SERVER_PORT
