#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

##############################################################################
# Libraries
##############################################################################

import json
import traceback
from urllib.parse import urlparse
from typing import Optional

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.locks import Lock


##############################################################################
# Constants and Variables and Functions
##############################################################################

# 强制使用 CurlAsyncHTTPClient，解决 SimpleAsyncHTTPClient 的 chunked/trailer 兼容性问题
AsyncHTTPClient.configure("tornado.curl_httpclient.CurlAsyncHTTPClient")

CADDY_ADMIN  = "http://127.0.0.1:2019"
CADDY_PREFIX = "/services"

_lock = Lock()                      # tornado.locks.Lock，支持 async with


async def caddy_find_server_by_site_address(site_address: str) -> Optional[str]:
    """
    根据 Caddyfile 中的站点地址（如 "https://example.com"）查找对应的 server_name。

    :param site_address: 站点地址，可带协议（https://example.com）或纯域名（example.com）
    :return: server_name（如 "srv0"）；未找到时返回 None
    """
    # 1. 解析出域名（host）
    domain = _extract_domain(site_address)
    if not domain:
        return None

    # 2. 拉取全部 server 配置
    try:
        resp = await _fetch("GET", f"{CADDY_ADMIN}/config/apps/http/servers")
        if resp.code >= 400:
            return None
        servers = json.loads(resp.body or b"{}") or {}
    except Exception:
        traceback.print_exc()
        return None

    # 3. 遍历每个 server，检查其 routes 中的 host 匹配器
    for server_name, cfg in servers.items():
        try:
            routes = cfg.get("routes") or []
            if _domain_in_routes(routes, domain):
                return server_name
        except Exception:
            traceback.print_exc()
            continue

    return None


def _extract_domain(site_address: str) -> Optional[str]:
    """
    从站点地址中提取域名。
    支持 "https://example.com"、"http://example.com"、"example.com"、":443" 等形式。
    """
    site_address = site_address.strip()
    if not site_address:
        return None

    # 如果看起来像 URL（含 ://），用 urlparse
    if "://" in site_address:
        parsed = urlparse(site_address)
        return parsed.hostname.lower() if parsed.hostname else None

    # 纯域名或带端口的域名
    # 去掉可能存在的端口（如 "example.com:8080"）
    if ":" in site_address:
        # 纯端口（如 ":443"）表示无域名，返回 None
        host_part = site_address.split(":", 1)[0]
        if not host_part:
            return None
        return host_part.lower()

    return site_address.lower()


def _domain_in_routes(routes: list, domain: str) -> bool:
    for route in routes or []:
        for matcher in route.get("match") or []:
            for host in matcher.get("host") or []:
                if _host_matches(host, domain):
                    return True
        for handler in route.get("handle") or []:
            if handler.get("handler") == "subroute":
                if _domain_in_routes(handler.get("routes") or [], domain):
                    return True
            elif handler.get("routes"):
                if _domain_in_routes(handler.get("routes") or [], domain):
                    return True
    return False


def _host_matches(pattern: str, domain: str) -> bool:
    pattern, domain = pattern.strip().lower(), domain.strip().lower()
    if pattern == domain:
        return True
    if not pattern or "*" not in pattern:
        return False
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return domain.endswith("." + suffix) and domain.count(".") == suffix.count(".") + 1
    if pattern.endswith("*"):
        return domain.startswith(pattern[:-1])
    return False


# ── 路由构造 ────────────────────────────────────────────────
def _rid(server_name: str, srv_id: str) -> str:
    """路由在 Caddy 内的唯一 @id，带 {server_name}- 前缀便于识别与批量清理。"""
    return f"{server_name}-{srv_id}"


def _prefix(srv_id: str) -> str:
    return f"{CADDY_PREFIX}/{srv_id}"


def _patterns(srv_id: str):
    p = _prefix(srv_id)
    return [p, f"{p}/*"]            # 同时匹配有无尾斜杠


def _routes_url(server_name: str) -> str:
    return f"{CADDY_ADMIN}/config/apps/http/servers/{server_name}/routes"


def _route_obj(server_name: str, srv_id: str, port: int) -> dict:
    return {
        "@id": _rid(server_name, srv_id),
        "match": [{"path": _patterns(srv_id)}],
        "handle": [
            {"handler": "rewrite",
             "strip_path_prefix": _prefix(srv_id)},
            {"handler": "reverse_proxy",
             "upstreams": [{"dial": f"127.0.0.1:{port}"}]},
        ],
    }


# ── 底层 HTTP 封装 ──────────────────────────────────────────
async def _fetch(method: str, url: str, body: bytes = None, timeout: float = 5.0):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = HTTPRequest(
        url,
        method=method,
        headers=headers,
        body=body,
        request_timeout=timeout,
    )
    return await AsyncHTTPClient().fetch(req, raise_error=False)


async def _get_routes(server_name):
    resp = await _fetch("GET", _routes_url(server_name))
    if resp.code >= 400:
        raise RuntimeError(f"GET routes failed: {resp.code}")
    data = json.loads(resp.body or b"null")
    return data or []


# ── 查询：遍历所有反向代理 ──────────────────────────────────
async def _walk_routes(routes, inherit_paths=None, inherit_strip=None):
    """
    递归展开 routes，抽取所有 reverse_proxy 条目。
    - subroute 内的子路由若自身没有 match，则继承父路径
    - rewrite / uri 的剥离前缀沿继承链向下传递
    返回 list[dict]: {id, paths, strip, upstream}
    """
    inherit_paths = inherit_paths or []
    out = []

    for route in routes or []:
        own_paths = []
        for m in route.get("match") or []:
            own_paths.extend(m.get("path") or [])
        paths = own_paths or inherit_paths

        strip = inherit_strip
        upstreams = []
        sub_routes = None

        for h in route.get("handle") or []:
            name = h.get("handler")
            if name == "rewrite":
                strip = h.get("strip_path_prefix") or strip
            elif name == "uri":
                strip = h.get("strip_prefix") or strip
            elif name == "reverse_proxy":
                for u in h.get("upstreams") or []:
                    if u.get("dial"):
                        upstreams.append(u["dial"])
            elif name == "subroute":
                sub_routes = h.get("routes") or []

        if sub_routes:
            out.extend(await _walk_routes(sub_routes, paths, strip))

        for up in upstreams:
            out.append({
                "id":       route.get("@id"),
                "paths":    list(paths),
                "strip":    strip,
                "upstream": up,
            })

    return out


async def caddy_is_running(timeout: float = 3.0) -> bool:
    """
    检测 Caddy 是否在运行（Admin API 可达即为运行）。

    - 返回 True：Caddy 进程存活 + Admin API 正常响应
    - 返回 False：进程未启动、Admin API 不可达、超时、或返回非 2xx

    注意：连接失败时 tornado 的 fetch 仍会抛异常（raise_error=False 只对 4xx/5xx 生效），
    必须 try/except 捕获，不能只看返回值。
    """
    try:
        resp = await _fetch("GET", f"{CADDY_ADMIN}/config/", timeout=timeout)
        return 200 <= resp.code < 300
    except Exception:
        traceback.print_exc()
        return False


async def caddy_list_proxies(server_name: str) -> list:
    """
    列出 Caddy 中指定 server 的全部反向代理路由。

    :param server_name: 要查询的 server 名（如 srv0）
    :return: list[dict] 每项含：
        server   所属 server 名（如 srv0）
        listen   监听地址列表（如 [":443"]）
        id       路由 @id（无 @id 则为 None）
        paths    匹配的路径列表
        strip    剥离前缀（无则 None）
        upstream 上游 dial 地址（如 127.0.0.1:8081）
    """
    result = []

    try:
        resp = await _fetch(
            "GET", f"{CADDY_ADMIN}/config/apps/http/servers/{server_name}"
        )
        if resp.code >= 400:
            return result
        cfg = json.loads(resp.body or b"{}") or {}
    except Exception:
        traceback.print_exc()
        return result

    listen = list(cfg.get("listen") or [])

    try:
        routes = await _get_routes(server_name)
    except Exception:
        traceback.print_exc()
        return result

    try:
        items = await _walk_routes(routes)
    except Exception:
        traceback.print_exc()
        return result

    for it in items:
        it["server"] = server_name
        it["listen"] = listen
        result.append(it)

    return result


# ── 格式化辅助 ──────────────────────────────────────────────
_PROXY_COLUMNS = ("SERVER", "LISTEN", "MATCH", "STRIP", "UPSTREAM", "@ID")


def _proxy_row(it: dict) -> list:
    return [
        it.get("server") or "-",
        ",".join(it.get("listen") or []) or "-",
        " ".join(it.get("paths") or []) or "-",
        it.get("strip") or "-",
        it.get("upstream") or "-",
        it.get("id") or "-",
    ]


def format_proxies_table(items: list) -> list:
    """返回二维列表（含表头），可直接喂给 PyWebIO 的 put_table。"""
    table = [list(_PROXY_COLUMNS)]
    for it in items:
        table.append(_proxy_row(it))
    return table


def format_proxies_text(items: list) -> str:
    """返回纯文本对齐表格，适合日志或命令行输出。"""
    if not items:
        return "(没有任何 reverse_proxy 路由)"

    rows = [_proxy_row(it) for it in items]
    widths = [
        max(len(r[i]) for r in (rows + [list(_PROXY_COLUMNS)]))
        for i in range(len(_PROXY_COLUMNS))
    ]

    def _fmt(row):
        return "  ".join(str(row[i]).ljust(widths[i]) for i in range(len(row)))

    lines = [_fmt(list(_PROXY_COLUMNS)),
             "-" * (sum(widths) + 2 * (len(widths) - 1))]
    lines.extend(_fmt(r) for r in rows)
    return "\n".join(lines)


# ── 对外接口（增删改，保持原样）─────────────────────────────
async def caddy_add_route(server_name: str, srv_id: str, port: int) -> bool:
    """幂等：先按 @id 删，再插到 routes 头部（优先于 file_server）。"""
    async with _lock:
        try:
            # 1) 幂等清理，404 忽略
            await _fetch("DELETE", f"{CADDY_ADMIN}/id/{_rid(server_name, srv_id)}")

            # 2) 读现有 routes 并剔除同名条目
            routes = await _get_routes(server_name)
            routes = [x for x in routes if x.get("@id") != _rid(server_name, srv_id)]

            # 3) 插到最前，确保先于 file_server 命中
            routes.insert(0, _route_obj(server_name, srv_id, port))

            # 4) 整体 PATCH
            resp = await _fetch(
                "PATCH", _routes_url(server_name),
                body=json.dumps(routes).encode("utf-8"),
            )
            if not (200 <= resp.code < 300):
                print(f"[caddy] PATCH {resp.code}: "
                      f"{(resp.body or b'').decode('utf-8', 'replace')[:1500]}", flush=True)
            return 200 <= resp.code < 300
        except Exception:
            traceback.print_exc()
            return False


async def caddy_del_route(server_name: str, srv_id: str) -> bool:
    """按 @id 精确删除，不做索引运算，天然并发安全。"""
    async with _lock:
        try:
            resp = await _fetch("DELETE", f"{CADDY_ADMIN}/id/{_rid(server_name, srv_id)}")
            return resp.code in (200, 404)   # 404 = 本来就没有，视为成功
        except Exception:
            traceback.print_exc()
            return False


async def caddy_bootstrap(server_name: str, children: dict) -> bool:
    """
    Caddy 重启 / caddy reload 后动态路由会丢失，
    启动时用当前子服务列表整体重放一次。
    children: {srv_id: port}
    """
    async with _lock:
        try:
            routes = await _get_routes(server_name)
            routes = [x for x in routes
                      if not str(x.get("@id", "")).startswith("xspawner-")]
            for sid, port in children.items():
                routes.insert(0, _route_obj(server_name, sid, port))

            resp = await _fetch(
                "PATCH", _routes_url(server_name),
                body=json.dumps(routes).encode("utf-8"),
            )
            return 200 <= resp.code < 300
        except Exception:
            traceback.print_exc()
            return False