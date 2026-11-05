# ============================================================================
# 访问口令校验（<PSW_FILE> 位于当前工作目录，PBKDF2-HMAC-SHA256 加盐哈希）
#   文件格式：pbkdf2_sha256$<iters>$<salt_hex>$<hash_hex>
#
# 口令文件的生成过程:
#
# cd /opt/xspawner    # 换成你的 WorkingDirectory（systemctl cat 里看）

# python3 - <<'PY' > admpw.txt
# import secrets, hashlib

# pw    = 'YOUR-PASSWORD'      # ← 改成你的口令
# iters = 200_000               # 迭代次数，200000 约 50~100ms/次
# salt  = secrets.token_bytes(16)

# dk = hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt, iters)
# print(f"pbkdf2_sha256${iters}${salt.hex()}${dk.hex()}")
# PY

# chmod 600 admpw.txt
# cat admpw.txt
# ============================================================================

import os
import time
import asyncio
import secrets
import hashlib
from typing import Optional, Dict, Tuple

from pywebio.input import input, PASSWORD
from pywebio.output import put_error
from pywebio.session import local, info

PSW_FILE = "admpw.txt"
PSW_PATH = os.path.join(os.getcwd(), PSW_FILE)

PSW_MAX_ATTEMPTS      = 3            # 累计失败达到此次数 → 锁定该 IP
PSW_LOCK_SECONDS      = 30 * 60      # 锁定时长：30 分钟
PSW_SESSION_ATTEMPTS  = 3            # 单次弹窗内最多尝试次数
PSW_CLEANUP_THRESHOLD = 1024         # 记录数超过此值时惰性清理

# 迭代次数合理区间：防止配置过弱，也防止恶意文件拖死 CPU
PSW_ITERS_MIN = 100_000
PSW_ITERS_MAX = 10_000_000

_psw_failures: Dict[str, dict] = {}
_psw_state_lock = asyncio.Lock()


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def _parse_pbkdf2(stored: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    解析 <PSW_FILE>，返回 (params, error)。
      params = {"iters": int, "salt": bytes, "hash": str(小写hex)}
    """
    parts = stored.split("$")
    if len(parts) != 4:
        return None, "格式应为 pbkdf2_sha256$iters$salt_hex$hash_hex"

    algo, iter_s, salt_hex, hash_hex = parts

    if algo.strip().lower() != "pbkdf2_sha256":
        return None, f"不支持的算法: {algo!r}，只接受 pbkdf2_sha256"

    try:
        iters = int(iter_s)
    except ValueError:
        return None, "iters 不是整数"
    if not (PSW_ITERS_MIN <= iters <= PSW_ITERS_MAX):
        return None, f"iters 超出范围 [{PSW_ITERS_MIN}, {PSW_ITERS_MAX}]"

    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return None, "salt_hex 不是合法十六进制"
    if len(salt) < 8:
        return None, "盐长度过短（至少 8 字节）"

    hash_hex = hash_hex.strip().lower()
    if len(hash_hex) != 64 or not _is_hex(hash_hex):
        return None, "hash_hex 不是 64 位十六进制"

    return {"iters": iters, "salt": salt, "hash": hash_hex}, None


def _verify(pw: str, params: dict) -> bool:
    calc = hashlib.pbkdf2_hmac(
        "sha256", (pw or "").encode("utf-8"), params["salt"], params["iters"]
    ).hex()
    return secrets.compare_digest(calc, params["hash"])


def _client_ip() -> str:
    """获取客户端真实 IP，兼容反向代理。"""
    try:
        ip = info.user_ip or "unknown"
        if ip in ("127.0.0.1", "::1", "localhost"):
            headers = getattr(info, "headers", None) or {}
            xff = (headers.get("X-Forwarded-For")
                   or headers.get("x-forwarded-for") or "")
            if xff:
                return xff.split(",")[0].strip()
        return ip
    except Exception:
        return "unknown"


async def _check_locked(ip: str) -> Optional[float]:
    async with _psw_state_lock:
        rec = _psw_failures.get(ip)
        if not rec:
            return None
        now = time.time()
        lock_until = rec.get("lock_until", 0)
        if lock_until > now:
            return lock_until - now
        if lock_until:
            _psw_failures.pop(ip, None)
        return None


async def _record_failure(ip: str) -> int:
    async with _psw_state_lock:
        now = time.time()
        rec = _psw_failures.setdefault(
            ip, {"count": 0, "lock_until": 0, "last_fail": 0}
        )
        rec["count"] += 1
        rec["last_fail"] = now
        if rec["count"] >= PSW_MAX_ATTEMPTS:
            rec["lock_until"] = now + PSW_LOCK_SECONDS
        _maybe_cleanup_locked(now)
        return rec["count"]


async def _reset_failures(ip: str) -> None:
    async with _psw_state_lock:
        _psw_failures.pop(ip, None)


def _maybe_cleanup_locked(now: float) -> None:
    if len(_psw_failures) < PSW_CLEANUP_THRESHOLD:
        return
    stale = [
        ip for ip, r in _psw_failures.items()
        if r.get("lock_until", 0) < now
        and (now - r.get("last_fail", 0)) > 3600
    ]
    for ip in stale:
        _psw_failures.pop(ip, None)


async def _require_password(srv) -> bool:
    """
    口令校验。返回 True 允许继续；False 拒绝访问。
    参数 srv 为 Spawner 实例，用于写日志。
    """
    # 1. 文件不存在 → 不启用
    if not os.path.isfile(PSW_PATH):
        return True

    # 2. 读取并 strip（echo 写入的换行）
    try:
        with open(PSW_PATH, "r", encoding="utf-8") as f:
            stored = f.read().strip()
    except Exception as e:
        srv.eLog(f"read {PSW_PATH} failed: {e}")
        put_error("口令文件读取失败，拒绝访问")
        return False

    # 3. 空文件 → 不启用
    if not stored:
        srv.wLog(f"{PSW_PATH} is empty, skip password check")
        return True

    # 4. 解析格式（配置错误尽早暴露，不让用户白输口令）
    params, err = _parse_pbkdf2(stored)
    if params is None:
        srv.eLog(f"{PSW_PATH} 格式错误: {err}")
        put_error(f"⛔ 口令文件格式错误：{err}")
        return False

    ip = _client_ip()

    # 5. 锁定检查
    remain = await _check_locked(ip)
    if remain is not None:
        minutes = int(remain // 60) + 1
        put_error(
            f"⛔ 该主机因口令错误次数过多已被临时锁定，"
            f"请约 {minutes} 分钟后重试（来源 IP: {ip}）"
        )
        srv.wLog(f"admpw rejected: ip {ip} locked, remain {remain:.0f}s")
        return False

    # 6. 本会话已验证
    if getattr(local, "admpw_verified", False):
        return True

    # 7. 弹窗校验
    for _ in range(PSW_SESSION_ATTEMPTS):
        try:
            pw = await input(
                "🔒 访问口令",
                type=PASSWORD,
                placeholder="请输入访问口令",
                validate=lambda v: None if (v or "") else "口令不能为空",
            )
        except Exception as e:
            srv.wLog(f"admpw input cancelled: {type(e).__name__}: {e}")
            return False

        # PBKDF2 是 CPU 密集，放线程池，避免阻塞 IOLoop
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, _verify, pw or "", params)

        if ok:
            local.admpw_verified = True
            await _reset_failures(ip)
            srv.iLog(f"admpw verified, ip {ip}")
            return True

        n = await _record_failure(ip)
        if n >= PSW_MAX_ATTEMPTS:
            put_error(
                f"⛔ 口令错误次数过多（该主机累计 {n} 次），"
                f"已锁定 {PSW_LOCK_SECONDS // 60} 分钟"
            )
            srv.wLog(f"admpw ip {ip} locked after {n} failures")
            return False

        put_error(
            f"❌ 口令错误（该主机累计 {n} 次，"
            f"达到 {PSW_MAX_ATTEMPTS} 次将锁定 "
            f"{PSW_LOCK_SECONDS // 60} 分钟）"
        )

    return False