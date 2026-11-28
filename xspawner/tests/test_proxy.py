from unittest import mock

from tornado.testing import AsyncHTTPTestCase
from tornado.web import Application, RequestHandler

from xspawner.tests.test_validation import _make_config
from xspawner.xspawner import ProxyHandler, XSpawner


COOKIES = [
    "session=abc; Path=/; HttpOnly",
    "theme=dark; Expires=Wed, 09 Jun 2032 10:18:14 GMT",
]


class Upstream(RequestHandler):
    def get(self):
        for cookie in COOKIES:
            self.add_header("Set-Cookie", cookie)

    def echo(self):
        self.write(self.request.method.encode() + b":" + self.request.body)

    post = put = patch = echo


class ProxyTests(AsyncHTTPTestCase):
    def get_app(self):
        return Application([
            (r"/services/([^/]+)/(.*)", ProxyHandler),
            (r"/upstream", Upstream),
        ])

    def setUp(self):
        super().setUp()
        config = _make_config(id="child", port=self.get_http_port())
        server = mock.Mock()
        server.getValue = mock.AsyncMock(return_value=config._asdict())
        patcher = mock.patch.object(XSpawner, "getServer", return_value=server)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_request_bodies(self):
        for method in ("POST", "PUT", "PATCH"):
            with self.subTest(method=method):
                response = self.fetch("/services/child/upstream", method=method, body=b"")
                self.assertEqual(response.code, 200)
                self.assertEqual(response.body, method.encode() + b":")

    def test_multiple_set_cookie_headers(self):
        response = self.fetch("/services/child/upstream")
        self.assertEqual(response.code, 200)
        self.assertEqual(response.headers.get_list("Set-Cookie"), COOKIES)
