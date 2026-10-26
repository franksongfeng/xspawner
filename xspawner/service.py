#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright © 2025 Song Feng.

##############################################################################
# Libraries
##############################################################################

import os
import sys
import subprocess
import shutil
import json
import re
import time
import shlex
import urllib3
from typing import Optional, Dict, List, Any

import psutil
import requests
import tornado.gen
import tornado.process

from xspawner.constants import Config, LOCAL_DB, LOG_FILE
from xspawner.utilities.log import Log


##############################################################################
# Constants and Variables and Functions
##############################################################################

# 屏蔽“内部自调用关闭证书校验”时 urllib3 的 InsecureRequestWarning。
# 仅影响 verify=False 的调用，外部调用不受影响。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

WORKING_DIR = "/opt/xspawner"
SERVICE_DIR = "/etc/systemd/system"
SERVICE_TMPL = """
[Unit]
Description={} service
After={}
Requires={}
PartOf={}

[Service]
Type=simple
WorkingDirectory={}

ExecStartPre=/bin/sleep 1.0s
{}ExecStart={}
Restart=on-failure

CPUQuota=50%
MemoryMax=512M

PrivateTmp=yes
ProtectHome=yes

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

# 包名 + extras + 版本约束；拒绝以 '-' 开头的 pip 选项注入
PIP_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.\-]*"
    r"(?:\[[A-Za-z0-9_.\-,]+\])?"
    r"(?:(?:[<>!=~]=?|===)[A-Za-z0-9_.\-*,]+)*$"
)


def is_valid_pip_pkg(pkg: str) -> bool:
    if not pkg or pkg.startswith("-"):
        return False
    return bool(PIP_PKG_RE.match(pkg))


def split_libs(libs: str) -> list:
    """
    按英文逗号分割 libs，去除每项首尾空白，过滤空串。

    统一入口，保证 service.py（systemd 安装）与 administrator.py（UI 预校验）
    对 libs 的解析口径一致。
    """
    text = (libs or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# 内部自调用辅助
#
# 云服务器（如阿里云 ECS）通常不支持 NAT hairpin：从实例内部访问自身的
# 公网 IP，流量要经 NAT 网关绕一圈回来，会超时。因此所有“对自身”的 HTTP
# 调用必须走 127.0.0.1。
#
# HTTPS 场景下，业务证书的 SAN 一般只签公网域名/IP，不包含 127.0.0.1，
# 因此内部调用必须 verify=False（本机→本机，无中间人风险）。
# ---------------------------------------------------------------------------

def get_local_url(port: int, use_ssl: bool = False) -> str:
    """本机自调用的基地址。"""
    return "{}://127.0.0.1:{}".format("https" if use_ssl else "http", port)


def get_local_verify(use_ssl: bool) -> bool:
    """
    内部自调用时 requests 的 verify 参数。

    - HTTP：True（对 http 无意义，保持默认）
    - HTTPS：False（证书 SAN 不含 127.0.0.1，必须关闭校验）
    """
    return not use_ssl


logger = Log("service", "file", LOG_FILE, "info")


def get_service_status(service_name: str) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["systemctl", "show", service_name, "--no-page"],
            capture_output=True,
            text=True,
            check=False
        )

        status = {}
        for line in result.stdout.split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                status[key] = value
        return status

    except Exception as e:
        logger.error(f"Failed to get service status: {e}")
        return {'name': service_name, 'error': str(e)}


def get_service_status_text(service_name: str, lines: int = 10) -> str:
    """
    获取 `systemctl status <id>` 的完整文本输出（人类可读）。
    """
    try:
        result = subprocess.run(
            ["systemctl", "status", service_name,
             "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, check=False,
        )
        return result.stdout or result.stderr
    except Exception as e:
        logger.error(f"Failed to get service status text: {e}")
        return ""


# ---------------- 异步执行 systemctl status（协程版） ----------------
async def get_service_status_text_async(service_name, lines: int = 20,
                           timeout: int = 5) -> str:
    """
    用 tornado.process.Subprocess 异步执行 `systemctl status <id>`，
    完全基于 Tornado 事件循环，不占用任何线程。

    为避免 systemctl 在某些环境下挂住不退出，命令前置 `timeout` 工具，
    强杀超时；并在 finally 中显式回收子进程，避免残留。
    """
    proc = tornado.process.Subprocess(
        ["timeout", str(timeout),
         "systemctl", "status", service_name,
         "-n", str(lines), "--no-pager"],
        stdout=tornado.process.Subprocess.STREAM,
        stderr=tornado.process.Subprocess.STREAM,
    )
    try:
        # 并发读两个流，避免某一端缓冲区满导致死锁
        out_b, err_b = await tornado.gen.multi([
            proc.stdout.read_until_close(),
            proc.stderr.read_until_close(),
        ])
    finally:
        # 确保子进程被回收，避免僵尸/残留
        try:
            await proc.wait_for_exit()
        except Exception:
            pass
    out = (out_b or b"").decode("utf-8", errors="replace")
    err = (err_b or b"").decode("utf-8", errors="replace")
    # systemctl status 在服务未运行时退出码非 0，但 stdout 仍有内容
    return out or err


def _run_systemctl(command: List[str]) -> bool:
    """执行 systemctl 命令的通用函数"""
    logger.info(f"_run_systemctl BEG {command}")
    try:
        subprocess.run(
            ["systemctl"] + command,
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"systemctl {' '.join(command)} successful")
        logger.info(f"_run_systemctl END true")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"systemctl {' '.join(command)} failed: {e.stderr}")
        return False


def reload_systemd() -> bool:
    """重新加载 systemd"""
    return _run_systemctl(["daemon-reload"])


def reload_service(service_name: str) -> bool:
    """重新加载 systemd"""
    return _run_systemctl(["reload", service_name])


def start_service(service_name: str) -> bool:
    """启动服务"""
    return _run_systemctl(["start", service_name])


def stop_service(service_name: str) -> bool:
    """停止服务"""
    return _run_systemctl(["stop", service_name])


def enable_service(service_name: str) -> bool:
    """启用服务开机自启"""
    return _run_systemctl(["enable", service_name])


def disable_service(service_name: str) -> bool:
    """禁用服务开机自启"""
    return _run_systemctl(["disable", service_name])


def get_exec_cmd(config: Config) -> str:
    cmd_parts = [
        "/usr/bin/python3", "-u", "-m", "xspawner",
        "--id", shlex.quote(config.id),
        "--plugin", shlex.quote(config.plugin),
        "--host", shlex.quote(config.host),
        "--port", str(config.port)
    ]

    if config.parent:
        cmd_parts.extend(["--parent", shlex.quote(config.parent)])

    cmd_parts.extend(["--access", shlex.quote(config.access)])
    cmd_parts.extend(["--log", "--severity", shlex.quote(config.severity)])

    if config.ssl and config.certfile and config.keyfile:
        cmd_parts.extend(["--ssl",
                          "--certfile", shlex.quote(config.certfile),
                          "--keyfile", shlex.quote(config.keyfile)])

    libs = getattr(config, "libs", "") or ""
    if libs:
        cmd_parts.extend(["--libs", shlex.quote(libs)])

    additional = getattr(config, "additional", "") or ""
    if additional:
        cmd_parts.extend(["--additional", shlex.quote(additional)])

    return " ".join(cmd_parts)


def _build_pip_install_pre(config: Config) -> str:
    """
    构造 ExecStartPre 行：在 systemd 启动服务进程前安装 libs。
    """
    libs = (getattr(config, "libs", "") or "")
    pkgs = split_libs(libs)
    if not pkgs:
        return ""

    for pkg in pkgs:
        if not is_valid_pip_pkg(pkg):
            raise ValueError(f"Invalid pip package spec in libs: {pkg!r}")

    cmd = " ".join([
        "/usr/bin/python3", "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
    ] + pkgs)
    return "ExecStartPre={}\n".format(cmd)


def generate_service_file(config: Config) -> str:
    """生成 systemd service 文件内容"""
    cmd = get_exec_cmd(config)
    pip_pre = _build_pip_install_pre(config)

    if config.parent:
        prior_service = f"{config.parent}.service"
    else:
        prior_service = 'network.target'
    service_content = SERVICE_TMPL.format(
        config.id, prior_service, prior_service, prior_service,
        WORKING_DIR, pip_pre, cmd
    )

    return service_content


def open_service(config: Config) -> bool:
    """写入 systemd service 文件"""
    logger.info(f"open_service BEG {config}")
    service_name = config.id
    try:
        os.makedirs(SERVICE_DIR, exist_ok=True)

        service_content = generate_service_file(config)

        service_path = f"{SERVICE_DIR}/{service_name}.service"
        with open(service_path, 'w') as f:
            f.write(service_content)
        logger.info(f"Wrote service file {service_path};")

        # 如果服务已在运行，先停掉（幂等）
        sts = get_service_status(service_name)
        if sts.get("ActiveState") == "active" and sts.get("LoadState") == "loaded":
            if not stop_service(service_name):
                logger.warning(f"Failed to stop existing {service_name}; "
                               f"continuing")

        if not reload_systemd():
            logger.error("Failed to reload systemd!")
            return False

        if not enable_service(service_name):
            logger.error(f"Failed to enable {service_name} service!")
            return False

        if not start_service(service_name):
            logger.error(f"Failed to start {service_name} service!")
            return False

        logger.info(f"Started {service_name} service successfully.")
        logger.info(f"open_service END true")
        return True

    except Exception as e:
        logger.error(f"Exception occurred when starting service: {e}")
        return False


def close_service(service_name: str) -> bool:
    """移除 systemd service 文件"""
    logger.info(f"close_service BEG {service_name}")
    try:
        service_path = f"{SERVICE_DIR}/{service_name}.service"

        stop_service(service_name)
        logger.info(f"Stopped service {service_name}")

        disable_service(service_name)
        logger.info(f"Disabled service {service_name}")

        if os.path.exists(service_path):
            os.unlink(service_path)
            logger.info(f"Deleted service file {service_name}")

        rt = reload_systemd()
        logger.info(f"close_service END {rt}")
        return rt
    except Exception as e:
        logger.error(f"Exception occurred when removing service: {e}")
        return False


def reset_service(service_name: str) -> bool:
    """重置服务（停止、禁用、重新加载）"""
    logger.info(f"reset_service BEG {service_name}")
    try:
        stop_service(service_name)
        disable_service(service_name)
        reload_systemd()
        logger.info(f"reset_service END true")
        return True
    except Exception as e:
        logger.info(f"Failed to reset service {service_name}: {e}")
        return False


def restart_service(service_name: str) -> bool:
    """重启服务"""
    return _run_systemctl(["restart", service_name])


def get_service_logs(service_name: str, lines: int = 50) -> Optional[str]:
    """获取服务日志"""
    try:
        result = subprocess.run(
            ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            check=False
        )
        logger.info(f"Get service logs {service_name}")
        return result.stdout
    except Exception as e:
        logger.error(f"Failed to get service logs: {e}")
        return None


def is_running_by_psutil(proc_name: str) -> bool:
    for proc in psutil.process_iter(['name', 'pid']):
        if proc.info['name'] == proc_name:
            return True
    return False


def wait_for_service_ready(srv_url: str, timeout: int = 60,
                           interval: float = 0.5,
                           verify: bool = True) -> bool:
    """
    轮询 /ping 直到服务就绪或超时。

    建议传入 127.0.0.1 基地址（见 get_local_url），避免依赖云厂商 NAT
    hairpin（从服务器自身访问公网 IP）。
    HTTPS 场景下传入 verify=False，否则会因证书 SAN 不含 127.0.0.1 失败。
    """
    deadline = time.time() + timeout
    attempt = 0
    last_err = None
    while time.time() < deadline:
        attempt += 1
        try:
            resp = requests.get(srv_url + "/ping", timeout=2, verify=verify)
            if resp.status_code == 200:
                logger.info(f"Service at {srv_url} is ready "
                            f"(attempt {attempt}, verify={verify})")
                return True
            last_err = f"HTTP {resp.status_code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(interval)
    logger.error(f"Service at {srv_url} not ready after {timeout}s; "
                 f"attempts={attempt}, last error={last_err}")
    return False


def wait_for_service_stopped(service_name: str, timeout: int = 30,
                             interval: float = 0.5) -> bool:
    """轮询 systemctl is-active，直到服务不再 active 或超时"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, check=False
        )
        state = result.stdout.strip()
        if state not in ("active", "activating", "deactivating"):
            logger.info(f"Service {service_name} is stopped (state={state})")
            return True
        time.sleep(interval)
    logger.error(f"Service {service_name} still active after {timeout}s")
    return False


if __name__ == "__main__":
    if len(sys.argv) == 3:
        op = sys.argv[1]
        file = sys.argv[2]
        if os.path.isfile(file):
            with open(file, 'r') as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError as e:
                    print(f"Error: invalid JSON in {file}")
                    print(f"  at line {e.lineno}, column {e.colno}: {e.msg}")
                    sys.exit(1)

            # 字段完整性预检（libs 缺失时给出明确提示）
            expected = list(Config._fields)
            missing = [k for k in expected if k not in data]
            if missing:
                print(f"Error: missing fields in {file}: {missing}")
                print(f"  expected fields: {expected}")
                sys.exit(1)

            cfg = Config(**data)

            # 对外展示用（安全组放行后浏览器可访问）
            srv_url = "{}://{}:{}".format(
                "https" if cfg.ssl else "http", cfg.host, cfg.port)
            # 内部自调用用（避免 NAT 回环；HTTPS 时关闭证书校验）
            local_url = get_local_url(cfg.port, cfg.ssl)
            local_verify = get_local_verify(cfg.ssl)

            srv_id = cfg.id
            sts = get_service_status(srv_id)
            logger.info(f"service status of {srv_id}: {sts}")

            if op == 'start':
                logger.info(f"start {srv_id} ...")
                if open_service(cfg):
                    # 内部健康检查走 127.0.0.1
                    if not wait_for_service_ready(local_url, timeout=60,
                                                  verify=local_verify):
                        logger.error(f"Error: service {srv_id} did not "
                                     f"become ready in time")
                        sys.exit(1)
                    print(f"Service {srv_id} is started, and its "
                          f"descendants will be started in turn.")
                    logs = get_service_logs(srv_id)
                    logger.info(f"Systemed service logs for {srv_id}:\n{logs}")

                    # 内部调用一律走 local_url + local_verify
                    child_ids = requests.post(
                        f"{local_url}/get_children", json={},
                        verify=local_verify).json()
                    for child_id in child_ids:
                        res = requests.post(
                            f"{local_url}/start_child",
                            json={"id": child_id},
                            verify=local_verify).json()
                        wait_for_service_ready(local_url, timeout=60,
                                               verify=local_verify)
                        logger.info(f"Service {child_id} is started: {res}")
                        print(f"Service {child_id} is started: {res}")
                else:
                    logger.error(f"Error: failed to start service {srv_id}")
                    sys.exit(1)

            elif op == 'stop':
                logger.info(f"stop {srv_id} ...")
                if sts.get("ActiveState") != "active":
                    logger.warning(f"Warning: service {srv_id} is not running!")
                    print(f"Service {srv_id} is not running!")
                    sys.exit(1)
                close_service(srv_id)
                logs = get_service_logs(srv_id)
                logger.info(f"Systemed service logs for {srv_id}:\n{logs}")
                logger.info(f"Service {srv_id} and its descendants are stopped.")
                print(f"Service {srv_id} and its descendants are stopped.")

            elif op == 'backup':
                logger.info(f"backup {srv_id} ...")
                try:
                    rt = requests.post(
                        f"{local_url}/backup_db",
                        json={"conn": f"sqlite://{LOCAL_DB}"},
                        verify=local_verify).json()
                    if rt:
                        print("Database backed up")
                        logger.info(f"backup {srv_id} done")
                    else:
                        print("No database file to backup")
                        logger.warning(f"backup {srv_id}: file not found "
                                       f"{LOCAL_DB}")
                        sys.exit(1)
                except Exception as e:
                    logger.error(f"backup {srv_id} failed: {e}")
                    print(f"Backup failed: {e}")
                    sys.exit(1)

            elif op == 'drop':
                logger.info(f"drop {srv_id} ...")
                if sts.get("ActiveState") != "active":
                    logger.warning(f"Warning: service {srv_id} is not running!")
                    print(f"Service {srv_id} is not running!")
                    sys.exit(1)

                child_ids = requests.post(
                    f"{local_url}/get_children", json={},
                    verify=local_verify).json()

                # ---- 破坏性操作前确认 ----
                print("")
                print("=" * 64)
                print("  WARNING: 'drop' is a DESTRUCTIVE operation! "
                      "Ensure data is backed up.")
                print("=" * 64)
                print(f"  Service id  : {srv_id}")
                print(f"  Children    : {child_ids if child_ids else '(none)'}")
                print("")
                print("This will:")
                print("  1. Stop every child service listed above")
                print("  2. Stop and remove this service's systemd unit")
                print(f"  3. DELETE the shared database")
                print("")
                print(f"Type the service id '{srv_id}' to confirm, "
                      f"anything else aborts:")
                try:
                    answer = input(">>> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nAborted.")
                    logger.warning(f"drop {srv_id} aborted (no confirmation)")
                    sys.exit(1)

                if answer != srv_id:
                    print(f"Aborted: confirmation '{answer}' does not "
                          f"match '{srv_id}'.")
                    logger.warning(f"drop {srv_id} aborted "
                                   f"(confirmation mismatch)")
                    sys.exit(1)
                # ---- ---- ---- ---- ----

                for child_id in child_ids:
                    requests.post(
                        f"{local_url}/stop_child",
                        json={"id": child_id},
                        verify=local_verify)
                    if not wait_for_service_stopped(child_id, timeout=30):
                        logger.warning(f"Warning: service {child_id} "
                                       f"still not fully stopped")
                    print(f"Service {child_id} is stopped.")

                # drop database
                requests.post(
                    f"{local_url}/drop_db",
                    json={"conn": f"sqlite://{LOCAL_DB}"},
                    verify=local_verify)

                if close_service(srv_id):
                    logs = get_service_logs(srv_id)
                    logger.info(f"Systemed service logs for {srv_id}:\n{logs}")
                    if not wait_for_service_stopped(srv_id, timeout=30):
                        logger.warning(f"Warning: service {srv_id} still "
                                       f"not fully stopped, "
                                       f"database may be locked")
                    print(f"Service {srv_id} is stopped.")
                    logger.info(f"Service {srv_id} and its descendants "
                                f"are dropped.")
                    print(f"Service {srv_id} and its descendants are dropped.")
                else:
                    logger.error(f"Error: failed to stop service {srv_id}!")
            else:
                logger.error(f"Error: invalid command {op}")
        else:
            logger.error(f"Error: file not found: {file}")
    else:
        logger.error(f"Error: miss command")