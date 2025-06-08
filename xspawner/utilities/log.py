# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

import logging
import logging.handlers
import datetime
import os

LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL
}

logger = None


def WriteLog(level, line):
    global logger
    if logger and logger.hasHandlers():
        {
            'debug': logger.debug,
            'info': logger.info,
            'warning': logger.warning,
            'error': logger.error,
            'critical': logger.critical
        }[level](line)

def int_to_7digit_str(num):
    return f"{str(num):>7}"

def MakeLine(lchar, content):
    return "{} {} {}|{}".format(datetime.datetime.now(), int_to_7digit_str(os.getpid()), lchar, content)

def PLine(content):
    global logger
    if logger and logger.hasHandlers():
        logger.info(content)

def DLine(content):
    WriteLog('debug', MakeLine('D', content))

def ILine(content):
    WriteLog('info', MakeLine('I', content))

def WLine(content):
    WriteLog('warning', MakeLine('W', content))

def ELine(content):
    WriteLog('error', MakeLine('E', content))

def CLine(content):
    WriteLog('critical', MakeLine('C', content))


def startLogger(logger_name, log_file='logging.out', log_level='info'):
    if log_level:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        global logger
        logger = logging.getLogger(logger_name)
        rf_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            mode='a',
            maxBytes=10485760,
            backupCount=7,
            encoding=None,
            delay=0
        )
        logger.addHandler(rf_handler)
        logger.setLevel(LEVELS[log_level])


def alterLevel(log_level):
    global logger
    logger.setLevel(LEVELS[log_level])
