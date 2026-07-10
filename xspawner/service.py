import os
import sys
import subprocess
import psutil
import json

from typing import Optional, Dict, Any
from xspawner.xspawner import Config, parse_ancestry

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
        print(f"Failed to get service status: {e}")
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
        print("systemd reload successful")
        return True
    except subprocess.CalledProcessError as e:
        print(f"systemd reload failed: {e.stderr}")
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
        print(f"Service {service_name} reloaded successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Service {service_name} reload failed: {e.stderr}")
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
        print(f"Service {service_name} started successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Service {service_name} failed to start: {e.stderr}")
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
        print(f"Service {service_name} stopped successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to stop service: {e.stderr}")
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
        print(f"Service {service_name} was set to start automatically on boot")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to enable service: {e.stderr}")
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
        print(f"Service {service_name} was disabled from starting automatically at boot")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to disable service: {e.stderr}")
        return False

def get_exec_cmd(config: Config) -> str:
    BASIC_CMD = "/usr/bin/python3 -u -m xspawner --name {} --app {} --host {} --port {}"
    cmd = BASIC_CMD.format(config.name, config.app, config.host, config.port)


    # add ancestry option
    if config.ancestry:
        cmd += " --ancestry {}".format(config.ancestry)

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
    if config.ancestry:
        ancestry_service, _ = parse_ancestry(config.ancestry)
        prior_service = f"{ancestry_service}.service"
    else:
        prior_service = 'network.target'
    service_content = SERVICE_TMPL.format(config.name, prior_service, prior_service, prior_service, WORKING_DIR, cmd)

    return service_content


def open_service(config: Config) -> dict:
    """写入 systemd service 文件"""
    rt = {"success": True, "info": ""}
    try:
        # 1. 创建服务目录（如果不存在）
        os.makedirs(SERVICE_DIR, exist_ok=True)

        # 2. 生成服务文件内容
        service_content = generate_service_file(config)
        print(service_content)

        # 3. 写入服务文件
        service_path = f"{SERVICE_DIR}/{config.name}.service"
        with open(service_path, 'w') as f:
            f.write(service_content)
        rt["info"] += f"Wrote service file {service_path}; "


        # 5. 重新加载 systemd
        if not reload_systemd():
            rt["info"] += "Failed to reload systemd!"
            rt["success"] = False
            return rt
        rt["info"] += "Reloaded systemd! "

        # 6. 启用服务开机自启
        if not enable_service(config.name): 
            rt["info"] += f"Failed to enable {config.name} service!"
            rt["success"] = False
            return rt

        # 7. 启动服务
        if not start_service(config.name):
            rt["info"] += f"Failed to start {config.name} service!"
            rt["success"] = False
            return rt

        rt["info"] += f"Started {config.name} service successfully"
        rt["success"] = True
        return rt
        
    except Exception as e:
        rt["info"] += f"Exception occurred when starting service: {e}"
        return rt


def close_service(service_name) -> dict:
    """移除 systemd service 文件"""
    rt = {"success": True, "info": ""}
    try:
        service_path = f"{SERVICE_DIR}/{service_name}.service"

        # 1. 停止服务
        stop_service(service_name)
        rt["info"] += f"Stopped service {service_name}; "

        # 2. 禁用开机自启
        disable_service(service_name)
        rt["info"] += f"Disabled service {service_name}; "

        # 3. 删除服务文件
        if os.path.exists(service_path):
            os.unlink(service_path)
            rt["info"] += f"Deleted service file {service_name}; "


        # 5. 重新加载 systemd
        reload_systemd()
        rt["info"] += f"Reloaded service {service_name}; "
        rt["success"] = True
        return rt
    except Exception as e:
        rt["info"] += f"Exception occurred when removing service: {e}"
        rt["success"] = False
        return rt


# 辅助函数：重置服务
def reset_service(service_name: str) -> dict:
    """重置服务（停止、禁用、重新加载）"""
    rt = {"success": True, "info": ""}
    try:
        stop_service(service_name)
        disable_service(service_name)
        reload_systemd()
        rt = {"success": True, "info": f"Reseted service {service_name}"}
        return rt
    except Exception as e:
        rt = {"success": False, "info": f"Failed to reset service {service_name}: {e}"}
        return rt


# 辅助函数：重启服务
def restart_service(service_name: str) -> dict:
    """重启服务"""
    rt = {"success": True, "info": ""}
    try:
        subprocess.run(
            ["systemctl", "restart", service_name],
            check=True,
            capture_output=True,
            text=True
        )
        rt = {"success": True, "info": f"Restarted service {service_name}"}
        return rt
    except subprocess.CalledProcessError as e:
        rt = {"success": False, "info": f"Failed to reset service {service_name}: {e}"}
        return rt


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
        return result.stdout
    except Exception as e:
        print(f"Failed to get service logs: {e}")
        return None


def is_running_by_psutil(proc_name: str):
    for proc in psutil.process_iter(['name', 'pid']):
        if proc.info['name'] == proc_name:
            return True



if __name__ == "__main__":
    if len(sys.argv) == 3:
        json_path = sys.argv[2]
        with open(json_path, 'r') as f:
            data = json.load(f)
            config = Config(**data)
        op = sys.argv[1]
        if op == 'open':
            rt = open_service(config)
            print(rt)
            get_service_logs(config.name)
        elif op == 'close':
            rt = close_service(config.name)
            print(rt)
        else:
            print(f"ERR: invalid command {op}")
    else:
        print("WARN: miss json_path argument")
