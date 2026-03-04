#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

# check parameters
if [ $# -lt 4 ]; then
    echo "Err: At least 4 parameters are required: name, app, host, port"
    exit 1
fi

DAEMON="false"

if [ $# -eq 6 ]; then
    SCHEME="http"
    CMD="sudo python3 -u -m xspawner --name $1 --app $2 --host $3 --port $4 --severity debug"
else
    SCHEME="https"
    CMD="sudo python3 -u -m xspawner --name $1 --app $2 --host $3 --port $4 --severity debug  --ssl --certfile $5 --keyfile $6"
fi

if [ "$DAEMON" = "true" ]; then
    REAL_CMD ="nohup "$CMD" &"
    $REAL_CMD
    sleep 1
    sudo python3 -m xspawner.apps.spawner.tests $SCHEME://$3:$4
else
    $CMD
fi
