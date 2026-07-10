import json
import http.client
import logging
from logging.handlers import RotatingFileHandler, HTTPHandler
from urllib.parse import urlparse
import os


LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL
}

class JsonHTTPHandler(HTTPHandler):
    def __init__(self, recorder):
        parsed = urlparse(recorder)
        secure = parsed.scheme.lower() == "https"
        host = parsed.netloc
        url = parsed.path if parsed.path else '/'
        method = "POST"
        super().__init__(host, url, method, secure)
        self.headers = {'Content-Type': 'application/json'}

    def emit(self, record):
        try:
            # 构造 JSON 数据
            formatted_log = self.format(record)
            payload = formatted_log.encode('utf-8')

            # 根据 secure 参数选择 HTTPS 连接
            conn_class = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
            conn = conn_class(self.host)
            conn.request(self.method, self.path, body=payload, headers=self.headers)
            response = conn.getresponse()
            response.read()
            conn.close()
        except Exception as e:
            self.handleError(record)


class Log(logging.Logger):
    """
    自定义日志记录器，继承自 logging.Logger
    支持将日志写入本地文件（RotatingFileHandler）或通过 HTTP 服务发送（AsyncJSONHttpHandler）
    """

    def __init__(self, name: str, category: str, recorder: str, severity: str = "info"):
        """
        初始化 Log 实例

        Args:
            name: 日志记录器名称，将作为 system 字段的值
            category: 日志存储类型，支持 'file' 或 'http'
            recorder: 日志存储地址
                - file: 文件路径
                - http: HTTP/HTTPS 服务 URL
            severity: 日志级别，支持 'debug', 'info', 'warning', 'error', 'critical'
        """
        # 调用父类初始化
        super().__init__(name)

        # 设置日志级别
        self.setLevel(LEVELS.get(severity, logging.DEBUG))

        # 验证 category 参数
        if category not in ['file', 'http']:
            raise ValueError(f"Invalid category")

        # 初始化处理器
        if category == 'file':
            # 验证 recorder 参数
            self._configure_handler(
                RotatingFileHandler,
                recorder,
                severity,
                '%(asctime)s.%(msecs)03d %(name)-8s %(levelname)-8s | %(message)s',
                mode='a',
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=7,
                encoding='utf-8'
            )
        elif category == 'http':
            # 验证 recorder 参数
            self._configure_handler(
                JsonHTTPHandler,
                recorder,
                severity,
                '{"timestamp":"%(asctime)s.%(msecs)03d","system":"%(name)s","severity":"%(levelname)s","content":"%(message)s"}'
            )

    def _configure_handler(self, klass, recorder, severity, fmt, **misc):
        try:
            self.handlers.clear()
            handler = klass(
                recorder,
                **misc
            )
            handler.setLevel(LEVELS.get(severity, logging.DEBUG))
            handler.setFormatter(logging.Formatter(fmt, datefmt='%Y-%m-%dT%H:%M:%S'))
            self.addHandler(handler)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize handler: {e}") from e
