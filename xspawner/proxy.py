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
# CADDY route id is like {srv_host}-{srv_id}
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
# Caddy 路由 @id 使用 host 前缀，避免多域名共享 Caddy 时冲突

def _rid(srv_host: str, srv_id: str) -> str:
    """
    路由在 Caddy 内的唯一 @id。

    格式：{srv_host}-{srv_id}
    - Caddy 的 @id 是全局命名空间
    - 用 host 做前缀，保证多域名共享 Caddy 时唯一
    - 同一 host 下 srv_id 必须唯一（由 DB 保证）
    """
    return f"{srv_host}-{srv_id}"


def _prefix(srv_id: str) -> str:
    return f"{CADDY_PREFIX}/{srv_id}"


def _patterns(srv_id: str):
    p = _prefix(srv_id)
    return [p, f"{p}/*"]            # 同时匹配有无尾斜杠


def _routes_url(server_name: str) -> str:
    return f"{CADDY_ADMIN}/config/apps/http/servers/{server_name}/routes"


def _route_obj(srv_host: str, srv_id: str, port: int) -> dict:
    """
    构造一条动态路由。

    ⚠️ match 必须同时包含 host 和 path：
    - Caddy 把同端口的多个 site block 合并到同一个 server（srv0），
      仅靠 path 无法区分域名。
    - 动态注入的路由不会自动继承 Caddyfile 里 site block 的域名约束，
      必须显式写 host 匹配器，否则多套系统的同名路径（如 /services/admin）
      会互相覆盖。
    """
    return {
        "@id": _rid(srv_host, srv_id),
        "match": [
            {
                "host": [srv_host],
                "path": _patterns(srv_id)
            }
        ],
        "handle": [
            {
                "handler": "rewrite",
                "strip_path_prefix": _prefix(srv_id)
            },
            {
                "handler": "reverse_proxy",
                "upstreams": [
                    {
                        "dial": f"127.0.0.1:{port}"
                    }
                ]
            },
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


# ── 对外接口（动态路由增删）─────────────────────────────────
async def caddy_add_route(srv_host: str, srv_id: str, port: int) -> bool:
    """幂等：先按 @id 删，再插到 routes 头部。"""
    async with _lock:
        try:
            server_name = await caddy_find_server_by_site_address(srv_host)
            if not server_name:
                print(f"[caddy] no server found for host {srv_host}", flush=True)
                return False

            rid = _rid(srv_host, srv_id)

            # 1) 幂等清理，404 忽略
            await _fetch("DELETE", f"{CADDY_ADMIN}/id/{rid}")

            # 2) 读现有 routes 并剔除同名条目
            routes = await _get_routes(server_name)
            routes = [x for x in routes if x.get("@id") != rid]

            # 3) 插到最前
            routes.insert(0, _route_obj(srv_host, srv_id, port))

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


async def caddy_del_route(srv_host: str, srv_id: str) -> bool:
    """按 @id 精确删除，不做索引运算，天然并发安全。"""
    async with _lock:
        try:
            resp = await _fetch("DELETE", f"{CADDY_ADMIN}/id/{_rid(srv_host, srv_id)}")
            return resp.code in (200, 404)   # 404 = 本来就没有，视为成功
        except Exception:
            traceback.print_exc()
            return False