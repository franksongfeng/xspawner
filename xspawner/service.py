import os
import sys
import subprocess
import psutil
import shutil
import json
import time
import shlex
import requests

from typing import Optional, Dict, List, Any
from xspawner.constants import Config, LOCAL_DB, LOG_FILE
from xspawner.utilities.log import Log

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
ExecStart={}
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

        return {
            'name': service_name,
            'active': status.get('ActiveState', 'unknown'),
            'status': status.get('SubState', 'unknown'),
            'loaded': status.get('LoadState', 'unknown'),
            'pid': status.get('MainPID', '0'),
            'memory': status.get('MemoryCurrent', '0'),
            'cpu': status.get('CPUUsageNSec', '0')
        }

    except Exception as e:
        logger.error(f"Failed to get service status: {e}")
        return {'name': service_name, 'error': str(e)}


def _run_systemctl(command: List[str]) -> bool:
    """执行 systemctl 命令的通用函数"""
    try:
        subprocess.run(
            ["systemctl"] + command,
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"systemctl {' '.join(command)} successful")
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


    # add parent option
    if config.parent:
        cmd_parts.extend(["--parent", shlex.quote(config.parent)])

    # add access option
    cmd_parts.extend(["--access", shlex.quote(config.access)])

    # add reportup option
    if config.reportup:
        cmd_parts.extend(["--reportup"])

    # add log options
    cmd_parts.extend(["--log", "--severity", shlex.quote(config.severity)])

    # add ssl options
    if config.ssl and config.certfile and config.keyfile:
        cmd_parts.extend(["--ssl", "--certfile", shlex.quote(config.certfile), "--keyfile", shlex.quote(config.keyfile)])

    return " ".join(cmd_parts)


def generate_service_file(config: Config) -> str:
    """生成 systemd service 文件内容"""
    cmd = get_exec_cmd(config)

    # python app executable
    if config.parent:
        prior_service = f"{config.parent}.service"
    else:
        prior_service = 'network.target'
    service_content = SERVICE_TMPL.format(config.id, prior_service, prior_service, prior_service, WORKING_DIR, cmd)

    return service_content


def open_service(config: Config) -> bool:
    """写入 systemd service 文件"""
    logger.info(f"open_service BEG {config}")
    service_name = config.id
    try:
        # 1. 创建服务目录（如果不存在）
        os.makedirs(SERVICE_DIR, exist_ok=True)

        # 2. 生成服务文件内容
        service_content = generate_service_file(config)
        logger.info(service_content)

        # 3. 写入服务文件
        service_path = f"{SERVICE_DIR}/{service_name}.service"
        with open(service_path, 'w') as f:
            f.write(service_content)
        logger.info(f"Wrote service file {service_path};")

        # 4. 停止（幂等），确保旧进程别停
        stop_service(service_name)

        # 5. 重新加载 systemd
        if not reload_systemd():
            logger.error("Failed to reload systemd!")
            return False

        # 6. 启用服务开机自启
        if not enable_service(service_name): 
            logger.error(f"Failed to enable {service_name} service!")
            return False

        # 7. 启动服务
        if not start_service(service_name):
            logger.error(f"Failed to start {service_name} service!")
            return False

        logger.info(f"Started {service_name} service successfully.")
        logger.info(f"open_service END True")
        return True
        
    except Exception as e:
        logger.error(f"Exception occurred when starting service: {e}")
        return False


def close_service(service_name: str) -> bool:
    """移除 systemd service 文件"""
    logger.info(f"close_service BEG {service_name}")
    try:
        service_path = f"{SERVICE_DIR}/{service_name}.service"

        # 1. 停止服务
        stop_service(service_name)
        logger.info(f"Stopped service {service_name}")

        # 2. 禁用开机自启
        disable_service(service_name)
        logger.info(f"Disabled service {service_name}")

        # 3. 删除服务文件
        if os.path.exists(service_path):
            os.unlink(service_path)
            logger.info(f"Deleted service file {service_name}")


        # 5. 重新加载 systemd
        rt = reload_systemd()
        logger.info(f"close_service END {rt}")
        return rt
    except Exception as e:
        logger.error(f"Exception occurred when removing service: {e}")
        return False


# 辅助函数：重置服务
def reset_service(service_name: str) -> bool:
    """重置服务（停止、禁用、重新加载）"""
    try:
        stop_service(service_name)
        disable_service(service_name)
        reload_systemd()
        logger.info(f"Reset service {service_name}")
        return True
    except Exception as e:
        logger.info(f"Failed to reset service {service_name}: {e}")
        return False


# 辅助函数：重启服务
def restart_service(service_name: str) -> bool:
    """重启服务"""
    return _run_systemctl(["restart", service_name])


# 辅助函数：获取服务日志
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

def delete_localdb(backup: bool = True):
    dbf = LOCAL_DB

    # backup db to be removed
    if backup and os.path.exists(dbf):
        shutil.copy2(dbf, f"{dbf}.bak")
        logger.info(f"Backup created at {dbf}.bak")

    files_to_delete = [dbf, f"{dbf}-shm", f"{dbf}-wal"]
    for fname in files_to_delete:
        if os.path.exists(fname):
            try:
                os.remove(fname)
                logger.info(f"successfully removed: {fname}")
            except OSError as e:
                logger.error(f"failed to remove {fname}: {e}")
        else:
            logger.warning(f"doesnt exist: {fname}")


def wait_for_service_ready(host: str, port: int, timeout: int = 60, interval: float = 0.5) -> bool:
    """轮询 /ping 直到服务就绪或超时"""
    url = "http://127.0.0.1:{}/ping".format(port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                logger.info(f"Service at {host}:{port} is ready")
                return True
        except Exception:
            pass
        time.sleep(interval)
    logger.error(f"Service at {host}:{port} not ready after {timeout}s")
    return False

def wait_for_service_stopped(service_name: str, timeout: int = 30, interval: float = 0.5) -> bool:
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
                data = json.load(f)
                cfg = Config(**data)
                srv_id = cfg.id
            if op == 'start':
                if open_service(cfg):
                    # wait service ready really
                    if not wait_for_service_ready(cfg.host, cfg.port, timeout=60):
                        print(f"Error: service {srv_id} did not become ready in time")
                        sys.exit(1)
                    logs = get_service_logs(srv_id)
                    print(f"Service logs:\n{logs}")
                    child_ids = requests.post("http://{}:{}/get_children".format(cfg.host, cfg.port), json={}).json()
                    for child_id in child_ids:
                        time.sleep(1)
                        res = requests.post("http://{}:{}/start_child".format(cfg.host, cfg.port), json={"id": child_id}).json()
                        print(f"Child {child_id} started: {res}")
                    print(f"Service {srv_id} is started. Its descendants should be started in turn.")
                else:
                    print(f"Error: failed to start service {srv_id}")
            elif op == 'stop':
                close_service(srv_id)
                print(f"Service {srv_id} and its descendants are stopped.")
            elif op == 'drop':
                child_ids = requests.post("http://{}:{}/get_children".format(cfg.host, cfg.port), json={}).json()
                for child_id in child_ids:
                    requests.post("http://{}:{}/stop_child".format(cfg.host, cfg.port), json={"id":child_id})
                if close_service(srv_id):
                    if not wait_for_service_stopped(srv_id, timeout=30):
                        print(f"Warning: service {srv_id} still not fully stopped, "
                              f"database may be locked")
                    delete_localdb()
                    print(f"The whole service {srv_id} including its descendants are dropped.")
                else:
                    print(f"Error: failed to stop service {srv_id}!")
            else:
                print(f"Error: invalid command {op}")
    else:
        print(f"Error: miss command")
