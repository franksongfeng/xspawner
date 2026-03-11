#!/bin/bash
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

# check parameters
if [ $# -lt 4 ]; then
    echo "Alert: At least 4 parameters are required: name, app, host, port"
    exit 1
fi

DAEMON="false"

if [ $# -eq 6 ]; then
    # ssl
    SCHEME="https"
    CMD="sudo python3 -u -m xspawner --name $1 --app $2 --host $3 --port $4 --ssl --certfile $5 --keyfile $6"
else
    # no ssl
    SCHEME="http"
    CMD="sudo python3 -u -m xspawner --name $1 --app $2 --host $3 --port $4"
fi

if [ "$DAEMON" = "true" ]; then
    # daemon
    eval "nohup "$CMD" &"
    sleep 1
    sudo python3 -m xspawner.apps.spawner.tests $SCHEME://$3:$4
else
    # debug
    $CMD
fi