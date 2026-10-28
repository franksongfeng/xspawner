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

from xspawner.constants import Config, LOCAL_DB, LOG_FILE, SUITE_ID
from xspawner.utilities.log import Log


##############################################################################
# Constants and Variables and Functions
##############################################################################

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVICE_DIR = "/etc/systemd/system"
SERVICE_TMPL = """
[Unit]
Description={}
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


def unit_name(srv_id: str) -> str:
    return f"{SUITE_ID}-{srv_id}"


logger = Log("service", "file", LOG_FILE, "info")


# ---------------------------------------------------------------------------
# 所有对外接口：参数为逻辑 id；内部 unit_name 转换
# ---------------------------------------------------------------------------

def get_service_status(srv_id: str) -> Dict[str, Any]:
    """srv_id 是逻辑 id（如 'admin'），内部转成 'xspawner-admin'。"""
    unit = unit_name(srv_id)
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--no-page"],
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
        return {'name': unit, 'error': str(e)}


def get_service_status_text(srv_id: str, lines: int = 10) -> str:
    """
    获取 `systemctl status <id>` 的完整文本输出（人类可读）。
    """
    unit = unit_name(srv_id)
    try:
        result = subprocess.run(
            ["systemctl", "status", unit,
             "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, check=False,
        )
        return result.stdout or result.stderr
    except Exception as e:
        logger.error(f"Failed to get service status text: {e}")
        return ""


async def get_service_status_text_async(srv_id, lines: int = 20,
                                        timeout: int = 5) -> str:
    """
    异步获取 systemctl status 文本，完全基于 Tornado 事件循环，不占用任何线程。
    为避免 systemctl 在某些环境下挂住不退出，命令前置 `timeout` 工具，
    强杀超时；并在 finally 中显式回收子进程，避免残留。
    """
    unit = unit_name(srv_id)
    proc = tornado.process.Subprocess(
        ["timeout", str(timeout),
         "systemctl", "status", unit,
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
    """重新读取整个 systemd 的配置"""
    return _run_systemctl(["daemon-reload"])


def reload_service(srv_id: str) -> bool:
    """重新加载服务"""
    return _run_systemctl(["reload", unit_name(srv_id)])

def start_service(srv_id: str) -> bool:
    """启动服务"""
    return _run_systemctl(["start", unit_name(srv_id)])

def stop_service(srv_id: str) -> bool:
    """停止服务"""
    return _run_systemctl(["stop", unit_name(srv_id)])

def enable_service(srv_id: str) -> bool:
    """启用服务开机自启"""
    return _run_systemctl(["enable", unit_name(srv_id)])

def disable_service(srv_id: str) -> bool:
    """禁用服务开机自启"""
    return _run_systemctl(["disable", unit_name(srv_id)])

def restart_service(srv_id: str) -> bool:
    return _run_systemctl(["restart", unit_name(srv_id)])


# ---------------------------------------------------------------------------
# 命令行构造：用 sys.executable 保证子进程跑在本 venv 里
# ---------------------------------------------------------------------------

def get_exec_cmd(config: Config) -> str:
    cmd_parts = [
        sys.executable, "-u", "-m", "xspawner",
        "--id", shlex.quote(config.id),
        "--plugin", shlex.quote(config.plugin),
        "--host", shlex.quote(config.host),
        "--port", str(config.port)
    ]

    if config.parent:
        cmd_parts.extend(["--parent", shlex.quote(config.parent)])

    cmd_parts.extend(["--access", shlex.quote(config.access)])
    cmd_parts.extend(["--log", "--severity", shlex.quote(config.severity)])

    if config.ssl:
        cmd_parts.extend(["--ssl"])

    if config.certfile and config.keyfile:
        cmd_parts.extend(["--certfile", shlex.quote(config.certfile),
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
    pkgs = split_libs(getattr(config, "libs", "") or "")
    if not pkgs:
        return ""

    for pkg in pkgs:
        if not is_valid_pip_pkg(pkg):
            raise ValueError(f"Invalid pip package spec in libs: {pkg!r}")

    cmd = " ".join([
        sys.executable, "-m", "pip", "install",
        "--no-input", "--disable-pip-version-check",
    ] + pkgs)
    return "ExecStartPre={}\n".format(cmd)


# ---------------------------------------------------------------------------
# systemd unit 文件生成
# ---------------------------------------------------------------------------

def generate_service_file(config: Config) -> str:
    """
    生成 systemd service 文件内容。
    注意 config.id / config.parent 都是逻辑 id，本函数内翻译为
    带 SUITE_ID 前缀的单元名，写入 Description / After / Requires / PartOf。
    """
    cmd = get_exec_cmd(config)
    pip_pre = _build_pip_install_pre(config)

    if config.parent:
        prior_service = f"{unit_name(config.parent)}.service"
    else:
        prior_service = 'network.target'

    description = f"{unit_name(config.id)} ({config.host}:{config.port})"

    work_dir = os.getcwd()

    service_content = SERVICE_TMPL.format(
        description, prior_service, prior_service, prior_service,
        work_dir, pip_pre, cmd
    )

    return service_content

def _unit_belongs_to(service_path: str, config: Config) -> bool:
    """防御：unit 已存在时，校验 host/port/WorkingDirectory 是否与本 Config 一致。"""
    try:
        with open(service_path) as f:
            content = f.read()
    except Exception:
        return False
    m = re.search(r'^ExecStart=.*$', content, re.MULTILINE)
    if not m:
        return False
    line = m.group(0)
    host_ok = (f"--host {config.host}" in line) or \
              (f"--host {shlex.quote(config.host)}" in line)
    port_ok = f"--port {config.port}" in line
    work_dir = os.getcwd()
    wd_ok   = f"WorkingDirectory={work_dir}" in content
    return host_ok and port_ok and wd_ok


def open_service(config: Config) -> bool:
    logger.info(f"open_service BEG {config}")
    service_name = unit_name(config.id)
    service_path = f"{SERVICE_DIR}/{service_name}.service"

    # 防御：已存在但不属于本 Config 时拒绝覆盖
    if os.path.exists(service_path) and not _unit_belongs_to(service_path, config):
        logger.error(
            f"Unit {service_name}.service exists with different host/port; "
            f"refusing to overwrite."
        )
        return False

    try:
        os.makedirs(SERVICE_DIR, exist_ok=True)
        service_path = f"{SERVICE_DIR}/{service_name}.service"
        with open(service_path, 'w') as f:
            service_content = generate_service_file(config)
            f.write(service_content)
        logger.info(f"Wrote service file {service_path};")

        sts = get_service_status(config.id)
        if sts.get("ActiveState") == "active" and sts.get("LoadState") == "loaded":
            if not stop_service(config.id):
                logger.warning(f"Failed to stop existing {service_name}; continuing")

        if not reload_systemd():
            logger.error("Failed to reload systemd!")
            return False
        if not enable_service(config.id):
            logger.error(f"Failed to enable {service_name} service!")
            return False
        if not start_service(config.id):
            logger.error(f"Failed to start {service_name} service!")
            return False

        logger.info(f"Started {service_name} service successfully.")
        logger.info(f"open_service END true")
        return True
    except Exception as e:
        logger.error(f"Exception occurred when starting service: {e}")
        return False


def close_service(srv_id: str) -> bool:
    """移除 systemd service 文件"""
    unit = unit_name(srv_id)
    logger.info(f"close_service BEG {unit}")
    try:
        service_path = f"{SERVICE_DIR}/{unit}.service"
        stop_service(srv_id)
        logger.info(f"Stopped service {unit}")
        disable_service(srv_id)
        logger.info(f"Disabled service {unit}")
        if os.path.exists(service_path):
            os.unlink(service_path)
            logger.info(f"Deleted service file {unit}")
        rt = reload_systemd()
        logger.info(f"close_service END {rt}")
        return rt
    except Exception as e:
        logger.error(f"Exception occurred when removing service: {e}")
        return False


def reset_service(srv_id: str) -> bool:
    """重置服务（停止、禁用、重新加载）"""
    logger.info(f"reset_service BEG {unit_name(srv_id)}")
    try:
        stop_service(srv_id)
        disable_service(srv_id)
        reload_systemd()
        logger.info(f"reset_service END true")
        return True
    except Exception as e:
        logger.info(f"Failed to reset service {unit_name(srv_id)}: {e}")
        return False


def get_service_logs(srv_id: str, lines: int = 50) -> Optional[str]:
    """获取服务日志"""
    unit = unit_name(srv_id)
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"],
            capture_output=True,
            text=True,
            check=False
        )
        logger.info(f"Get service logs {unit}")
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


def wait_for_service_stopped(srv_id: str, timeout: int = 30,
                             interval: float = 0.5) -> bool:
    """轮询 systemctl is-active，直到服务不再 active 或超时"""
    unit = unit_name(srv_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, check=False
        )
        state = result.stdout.strip()
        if state not in ("active", "activating", "deactivating"):
            logger.info(f"Service {unit} is stopped (state={state})")
            return True
        time.sleep(interval)
    logger.error(f"Service {unit} still active after {timeout}s")
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

            # 内部自调用用（避免 NAT 回环；当内部 HTTPS 时关闭证书校验）
            local_ssl = bool(cfg.ssl and cfg.certfile and cfg.keyfile)
            local_url = get_local_url(cfg.port, local_ssl)
            local_verify = get_local_verify(local_ssl)

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
                          f"descendants will be started in turn.\n")
                    logs = get_service_logs(srv_id, 10)
                    print(f"Systemed logs for service {srv_id}:\n{logs}")

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
                    print(f"Service {srv_id} is not running!\n")
                    sys.exit(1)
                close_service(srv_id)
                logs = get_service_logs(srv_id, 10)
                print(f"Systemed logs for service {srv_id}:\n{logs}")
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

                print("")
                print("=" * 64)
                print("  WARNING: 'drop' is a DESTRUCTIVE operation! "
                      "Ensure data is backed up.")
                print("=" * 64)
                print(f"  Service id  : {srv_id}")
                print(f"  Systemd unit: {unit_name(srv_id)}")
                print(f"  Database    : {LOCAL_DB}")
                print(f"  Children    : {child_ids if child_ids else '(none)'}")
                print("")
                print("This will:")
                print("  1. Stop every child service listed above")
                print("  2. Stop and remove this service's systemd unit")
                print(f"  3. DELETE this suite's database: {LOCAL_DB}")
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
                    logger.warning(f"drop {srv_id} aborted (confirmation mismatch)")
                    sys.exit(1)

                for child_id in child_ids:
                    requests.post(
                        f"{local_url}/stop_child",
                        json={"id": child_id}, verify=local_verify)
                    if not wait_for_service_stopped(child_id, timeout=30):
                        logger.warning(f"Warning: service {child_id} "
                                       f"still not fully stopped")
                    print(f"Service {child_id} is stopped.")

                requests.post(
                    f"{local_url}/drop_db",
                    json={"conn": f"sqlite://{LOCAL_DB}"}, verify=local_verify)

                if close_service(srv_id):
                    logs = get_service_logs(srv_id, 10)
                    print(f"Systemed logs for service {srv_id}:\n{logs}")
                    if not wait_for_service_stopped(srv_id, timeout=30):
                        logger.warning(f"Warning: service {srv_id} still "
                                       f"not fully stopped, "
                                       f"database may be locked")
                    print(f"Service {srv_id} is stopped.")
                    logger.info(f"Service {srv_id} and its descendants are dropped.")
                    print(f"Service {srv_id} and its descendants are dropped.")
                else:
                    logger.error(f"Error: failed to stop service {srv_id}!")
            else:
                logger.error(f"Error: invalid command {op}")
        else:
            logger.error(f"Error: file not found: {file}")
    else:
        logger.error(f"Error: miss command")