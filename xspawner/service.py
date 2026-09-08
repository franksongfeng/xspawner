import os
import sys
import subprocess
import psutil
import json
import time

from typing import Optional, Dict, Any
from xspawner.xspawner import Config
from xspawner.constants import LOCAL_DB, LOG_FILE
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


def reload_systemd() -> bool:
    """重新加载 systemd"""
    try:
        subprocess.run(
            ["systemctl", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info("systemd reload successful")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"systemd reload failed: {e.stderr}")
        return False

def reload_service(service_name: str) -> bool:
    """重新加载 systemd"""
    try:
        subprocess.run(
            ["systemctl", "reload", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Service {service_name} reloaded successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Service {service_name} reload failed: {e.stderr}")
        return False

def start_service(service_name: str) -> bool:
    """启动服务"""
    try:
        subprocess.run(
            ["systemctl", "start", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Service {service_name} started successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Service {service_name} failed to start: {e.stderr}")
        return False

def stop_service(service_name: str) -> bool:
    """停止服务"""
    try:
        subprocess.run(
            ["systemctl", "stop", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Service {service_name} stopped successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to stop service: {e.stderr}")
        return False

def enable_service(service_name: str) -> bool:
    """启用服务开机自启"""
    try:
        subprocess.run(
            ["systemctl", "enable", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Service {service_name} was set to start automatically on boot")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to enable service: {e.stderr}")
        return False

def disable_service(service_name: str) -> bool:
    """禁用服务开机自启"""
    try:
        subprocess.run(
            ["systemctl", "disable", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Service {service_name} was disabled from starting automatically at boot")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to disable service: {e.stderr}")
        return False

def get_exec_cmd(config: Config) -> str:
    BASIC_CMD = "/usr/bin/python3 -u -m xspawner --id {} --plugin {} --host {} --port {}"
    cmd = BASIC_CMD.format(config.id, config.plugin, config.host, config.port)


    # add parent option
    if config.parent:
        cmd += " --parent {}".format(config.parent)

    # add access option
    cmd += " --access {}".format(config.access)

    # add reportup option
    if config.reportup:
        cmd += " --reportup"

    # add log options
    cmd += " --log --severity {}".format(config.severity)

    # add ssl options
    if config.ssl and config.certfile and config.keyfile:
        cmd += " --ssl --certfile {} --keyfile {}".format(config.certfile, config.keyfile)

    return cmd


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


def open_services():
    # TODO: open all services on local db
    pass

def close_service(service_name) -> bool:
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

def close_services():
    # TODO: close all services on local db
    pass

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
    try:
        subprocess.run(
            ["systemctl", "restart", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        logger.info(f"Restarted service {service_name}")
        return True
    except subprocess.CalledProcessError as e:
        logger.info( f"Failed to restart service {service_name}: {e}")
        return False


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


def is_running_by_psutil(proc_name: str):
    for proc in psutil.process_iter(['name', 'pid']):
        if proc.info['name'] == proc_name:
            return True


def delete_localdb():
    dbf = LOCAL_DB
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


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        op = sys.argv[1]
        config = None
        if len(sys.argv) == 3:
            json_path = sys.argv[2]
            with open(json_path, 'r') as f:
                data = json.load(f)
                config = Config(**data)
        if op == 'open':
            if config:
                delete_localdb()
                time.sleep(1)
                if open_service(config):
                    logs = get_service_logs(config.id)
                    if logs:
                        print(f"Service logs:\n{logs}")
                    print(f"Service {config} is open.")
            else:
                open_services()
        elif op == 'close':
            if config:
                if close_service(config.id):
                    print(f"Service {config} is close.")
            else:
                close_services()
        else:
            logger.error(f"ERR: invalid command {op}")
    else:
        logger.error(f"ERR: miss command")
