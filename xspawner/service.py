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

        # return value is like the following including ActiveState, SubState, LoadState, MainPID, ControlPID, MemoryCurrent (byte) and CPUUsageNSec (ns)
        # {
        #     'Type': 'simple',
        #     'Restart': 'on-failure',

        #     'NotifyAccess': 'none',
        #     'RestartUSec': '100ms',
        #     'TimeoutStartUSec': '1min 30s',
        #     'TimeoutStopUSec': '1min 30s',
        #     'TimeoutAbortUSec': '1min 30s',
        #     'RuntimeMaxUSec': 'infinity',
        #     'WatchdogUSec': '0',
        #     'WatchdogTimestampMonotonic': '0',
        #     'RootDirectoryStartOnly': 'no',
        #     'RemainAfterExit': 'no',
        #     'GuessMainPID': 'yes',

        #     'MainPID': '6699',
        #     'ControlPID': '0',

        #     'FileDescriptorStoreMax': '0',
        #     'NFileDescriptorStore': '0',
        #     'StatusErrno': '0',

        #     'Result': 'success',
        #     'ReloadResult': 'success',
        #     'CleanResult': 'success',

        #     'UID': '[not set]',
        #     'GID': '[not set]',
        #     'NRestarts': '0',
        #     'OOMPolicy': 'stop',

        #     'ExecMainStartTimestamp': 'Mon 2026-09-14 16:14:27 CST',
        #     'ExecMainStartTimestampMonotonic': '3690987359',
        #     'ExecMainExitTimestampMonotonic': '0',
        #     'ExecMainPID': '6699',
        #     'ExecMainCode': '0',
        #     'ExecMainStatus': '0',
        #     'ExecStartPre': '{ path=/bin/sleep ; argv[]=/bin/sleep 1.0s ; ignore_errors=no ; start_time=[Mon 2026-09-14 16:14:26 CST] ; stop_time=[Mon 2026-09-14 16:14:27 CST] ; pid=6698 ; code=exited ; status=0 }',
        #     'ExecStartPreEx': '{ path=/bin/sleep ; argv[]=/bin/sleep 1.0s ; flags= ; start_time=[Mon 2026-09-14 16:14:26 CST] ; stop_time=[Mon 2026-09-14 16:14:27 CST] ; pid=6698 ; code=exited ; status=0 }',
        #     'ExecStart': '{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -u -m xspawner --id xspawner --plugin supervisor --host 192.168.1.51 --port 8668 --access 0.0.0.0 --log --severity debug ; ignore_errors=no ; start_time=[Mon 2026-09-14 16:14:27 CST] ; stop_time=[n/a] ; pid=6699 ; code=(null) ; status=0/0 }',
        #     'ExecStartEx': '{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -u -m xspawner --id xspawner --plugin supervisor --host 192.168.1.51 --port 8668 --access 0.0.0.0 --log --severity debug ; flags= ; start_time=[Mon 2026-09-14 16:14:27 CST] ; stop_time=[n/a] ; pid=6699 ; code=(null) ; status=0/0 }',
        #     'Slice': 'system.slice',
        #     'ControlGroup': '/system.slice/xspawner.service',
        #     'MemoryCurrent': '55558144',
        #     'CPUUsageNSec': '[not set]',

        #     'EffectiveCPUs': '',
        #     'EffectiveMemoryNodes': '',
        #     'TasksCurrent': '3',
        #     'IPIngressBytes': '[no data]',
        #     'IPIngressPackets': '[no data]',
        #     'IPEgressBytes': '[no data]',
        #     'IPEgressPackets': '[no data]',
        #     'IOReadBytes': '18446744073709551615',
        #     'IOReadOperations': '18446744073709551615',
        #     'IOWriteBytes': '18446744073709551615',
        #     'IOWriteOperations': '18446744073709551615',
        #     'Delegate': 'no',
        #     'CPUAccounting': 'no',
        #     'CPUWeight': '[not set]',
        #     'StartupCPUWeight': '[not set]',
        #     'CPUShares': '[not set]',
        #     'StartupCPUShares': '[not set]',
        #     'CPUQuotaPerSecUSec': '500ms',
        #     'CPUQuotaPeriodUSec': 'infinity',
        #     'AllowedCPUs': '',
        #     'AllowedMemoryNodes': '',
        #     'IOAccounting': 'no',
        #     'IOWeight': '[not set]',
        #     'StartupIOWeight': '[not set]',
        #     'BlockIOAccounting': 'no',
        #     'BlockIOWeight': '[not set]',
        #     'StartupBlockIOWeight': '[not set]',
        #     'MemoryAccounting': 'yes',
        #     'DefaultMemoryLow': '0',
        #     'DefaultMemoryMin': '0',
        #     'MemoryMin': '0',
        #     'MemoryLow': '0',
        #     'MemoryHigh': 'infinity',
        #     'MemoryMax': '536870912',
        #     'MemorySwapMax': 'infinity',
        #     'MemoryLimit': 'infinity',
        #     'DevicePolicy': 'auto',
        #     'TasksAccounting': 'yes',
        #     'TasksMax': '9409',
        #     'IPAccounting': 'no',
        #     'UMask': '0022',
        #     'LimitCPU': 'infinity',
        #     'LimitCPUSoft': 'infinity',
        #     'LimitFSIZE': 'infinity',
        #     'LimitFSIZESoft': 'infinity',
        #     'LimitDATA': 'infinity',
        #     'LimitDATASoft': 'infinity',
        #     'LimitSTACK': 'infinity',
        #     'LimitSTACKSoft': '8388608',
        #     'LimitCORE': 'infinity',
        #     'LimitCORESoft': '0',
        #     'LimitRSS': 'infinity',
        #     'LimitRSSSoft': 'infinity',
        #     'LimitNOFILE': '524288',
        #     'LimitNOFILESoft': '1024',
        #     'LimitAS': 'infinity',
        #     'LimitASSoft': 'infinity',
        #     'LimitNPROC': '31364',
        #     'LimitNPROCSoft': '31364',
        #     'LimitMEMLOCK': '65536',
        #     'LimitMEMLOCKSoft': '65536',
        #     'LimitLOCKS': 'infinity',
        #     'LimitLOCKSSoft': 'infinity',
        #     'LimitSIGPENDING': '31364',
        #     'LimitSIGPENDINGSoft': '31364',
        #     'LimitMSGQUEUE': '819200',
        #     'LimitMSGQUEUESoft': '819200',
        #     'LimitNICE': '0',
        #     'LimitNICESoft': '0',
        #     'LimitRTPRIO': '0',
        #     'LimitRTPRIOSoft': '0',
        #     'LimitRTTIME': 'infinity',
        #     'LimitRTTIMESoft': 'infinity',
        #     'WorkingDirectory': '/opt/xspawner',
        #     'OOMScoreAdjust': '0',
        #     'Nice': '0',
        #     'IOSchedulingClass': '0',
        #     'IOSchedulingPriority': '0',
        #     'CPUSchedulingPolicy': '0',
        #     'CPUSchedulingPriority': '0',
        #     'CPUAffinity': '',
        #     'CPUAffinityFromNUMA': 'no',
        #     'NUMAPolicy': 'n/a',
        #     'NUMAMask': '',
        #     'TimerSlackNSec': '50000',
        #     'CPUSchedulingResetOnFork': 'no',
        #     'NonBlocking': 'no',
        #     'StandardInput': 'null',
        #     'StandardInputData': '',
        #     'StandardOutput': 'journal',
        #     'StandardError': 'journal',
        #     'TTYReset': 'no',
        #     'TTYVHangup': 'no',
        #     'TTYVTDisallocate': 'no',
        #     'SyslogPriority': '30',
        #     'SyslogLevelPrefix': 'yes',
        #     'SyslogLevel': '6',
        #     'SyslogFacility': '3',
        #     'LogLevelMax': '-1',
        #     'LogRateLimitIntervalUSec': '0',
        #     'LogRateLimitBurst': '0',
        #     'SecureBits': '0',
        #     'CapabilityBoundingSet': 'cap_chown cap_dac_override cap_dac_read_search cap_fowner cap_fsetid cap_kill cap_setgid cap_setuid cap_setpcap cap_linux_immutable cap_net_bind_service cap_net_broadcast cap_net_admin cap_net_raw cap_ipc_lock cap_ipc_owner cap_sys_module cap_sys_rawio cap_sys_chroot cap_sys_ptrace cap_sys_pacct cap_sys_admin cap_sys_boot cap_sys_nice cap_sys_resource cap_sys_time cap_sys_tty_config cap_mknod cap_lease cap_audit_write cap_audit_control cap_setfcap cap_mac_override cap_mac_admin cap_syslog cap_wake_alarm cap_block_suspend cap_audit_read 0x26 0x27 0x28',
        #     'AmbientCapabilities': '',
        #     'DynamicUser': 'no',
        #     'RemoveIPC': 'no',
        #     'MountFlags': '',
        #     'PrivateTmp': 'yes',
        #     'PrivateDevices': 'no',
        #     'ProtectKernelTunables': 'no',
        #     'ProtectKernelModules': 'no',
        #     'ProtectKernelLogs': 'no',
        #     'ProtectControlGroups': 'no',
        #     'PrivateNetwork': 'no',
        #     'PrivateUsers': 'no',
        #     'PrivateMounts': 'no',
        #     'ProtectHome': 'yes',
        #     'ProtectSystem': 'no',
        #     'SameProcessGroup': 'no',
        #     'UtmpMode': 'init',
        #     'IgnoreSIGPIPE': 'yes',
        #     'NoNewPrivileges': 'no',
        #     'SystemCallErrorNumber': '0',
        #     'LockPersonality': 'no',
        #     'RuntimeDirectoryPreserve': 'no',
        #     'RuntimeDirectoryMode': '0755',
        #     'StateDirectoryMode': '0755',
        #     'CacheDirectoryMode': '0755',
        #     'LogsDirectoryMode': '0755',
        #     'ConfigurationDirectoryMode': '0755',
        #     'TimeoutCleanUSec': 'infinity',
        #     'MemoryDenyWriteExecute': 'no',
        #     'RestrictRealtime': 'no',
        #     'RestrictSUIDSGID': 'no',
        #     'RestrictNamespaces': 'no',
        #     'MountAPIVFS': 'no',
        #     'KeyringMode': 'private',
        #     'ProtectHostname': 'no',
        #     'KillMode': 'control-group',
        #     'KillSignal': '15',
        #     'RestartKillSignal': '15',
        #     'FinalKillSignal': '9',
        #     'SendSIGKILL': 'yes',
        #     'SendSIGHUP': 'no',
        #     'WatchdogSignal': '6',

        #     'Id': 'xspawner.service',
        #     'Names': 'xspawner.service',
        #     'Requires': 'sysinit.target network.target system.slice -.mount',
        #     'PartOf': 'network.target',
        #     'WantedBy': 'multi-user.target',
        #     'Conflicts': 'shutdown.target',
        #     'Before': 'shutdown.target multi-user.target',
        #     'After': 'systemd-tmpfiles-setup.service basic.target system.slice -.mount systemd-journald.socket network.target sysinit.target',
        #     'RequiresMountsFor': '/tmp /var/tmp /opt/xspawner',
        #     'Description': 'xspawner service',
        #     'LoadState': 'loaded',
        #     'ActiveState': 'active',
        #     'SubState': 'running',

        #     'FragmentPath': '/etc/systemd/system/xspawner.service',
        #     'UnitFileState': 'enabled',
        #     'UnitFilePreset': 'enabled',
        #     'StateChangeTimestamp': 'Mon 2026-09-14 16:14:27 CST',
        #     'StateChangeTimestampMonotonic': '3690990080',
        #     'InactiveExitTimestamp': 'Mon 2026-09-14 16:14:26 CST',
        #     'InactiveExitTimestampMonotonic': '3689967036',
        #     'ActiveEnterTimestamp': 'Mon 2026-09-14 16:14:27 CST',
        #     'ActiveEnterTimestampMonotonic': '3690987556',
        #     'ActiveExitTimestamp': 'Mon 2026-09-14 16:14:25 CST',
        #     'ActiveExitTimestampMonotonic': '3688902975',
        #     'InactiveEnterTimestamp': 'Mon 2026-09-14 16:14:25 CST',
        #     'InactiveEnterTimestampMonotonic': '3688912905',
        #     'CanStart': 'yes',
        #     'CanStop': 'yes',
        #     'CanReload': 'no',
        #     'CanIsolate': 'no',
        #     'StopWhenUnneeded': 'no',
        #     'RefuseManualStart': 'no',
        #     'RefuseManualStop': 'no',
        #     'AllowIsolate': 'no',
        #     'DefaultDependencies': 'yes',
        #     'OnFailureJobMode': 'replace',
        #     'IgnoreOnIsolate': 'no',
        #     'NeedDaemonReload': 'no',
        #     'JobTimeoutUSec': 'infinity',
        #     'JobRunningTimeoutUSec': 'infinity',
        #     'JobTimeoutAction': 'none',
        #     'ConditionResult': 'yes',
        #     'AssertResult': 'yes',
        #     'ConditionTimestamp': 'Mon 2026-09-14 16:14:26 CST',
        #     'ConditionTimestampMonotonic': '3689964344',
        #     'AssertTimestamp': 'Mon 2026-09-14 16:14:26 CST',
        #     'AssertTimestampMonotonic': '3689964344',
        #     'Transient': 'no',
        #     'Perpetual': 'no',
        #     'StartLimitIntervalUSec': '10s',
        #     'StartLimitBurst': '5',
        #     'StartLimitAction': 'none',
        #     'FailureAction': 'none',
        #     'SuccessAction': 'none',
        #     'InvocationID': 'aa6840e61ae542d994fbed1cd313e394',
        #     'CollectMode': 'inactive'
        # }
        return status

    except Exception as e:
        logger.error(f"Failed to get service status: {e}")
        return {'name': service_name, 'error': str(e)}


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


    # add parent option
    if config.parent:
        cmd_parts.extend(["--parent", shlex.quote(config.parent)])

    # add access option
    cmd_parts.extend(["--access", shlex.quote(config.access)])


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
        logger.info("service content: {}".format(service_content))

        # 3. 写入服务文件
        service_path = f"{SERVICE_DIR}/{service_name}.service"
        with open(service_path, 'w') as f:
            f.write(service_content)
        logger.info(f"Wrote service file {service_path};")

        # 4. 确保旧进程别停（幂等）
        sts = get_service_status(service_name)
        if sts.get("ActiveState") == "active" and sts.get("LoadState") == "loaded":
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


def wait_for_service_ready(srv_url: str, timeout: int = 60, interval: float = 0.5) -> bool:
    """轮询 /ping 直到服务就绪或超时"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(srv_url + "/ping", timeout=2)
            if resp.status_code == 200:
                logger.info(f"Service at {srv_url} is ready")
                return True
        except Exception:
            pass
        time.sleep(interval)
    logger.error(f"Service at {srv_url} not ready after {timeout}s")
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
            srv_url = "{}://{}:{}".format("https" if cfg.ssl else "http", cfg.host, cfg.port)
            srv_id = cfg.id
            sts = get_service_status(srv_id)
            logger.info(f"service status of {srv_id}: {sts}")
            if op == 'start':
                logger.info(f"start {srv_id} ...")
                if open_service(cfg):
                    # wait service ready really
                    if not wait_for_service_ready(srv_url, timeout=60):
                        logger.error(f"Error: service {srv_id} did not become ready in time")
                        sys.exit(1)
                    print(f"Service {srv_id} is started, and its descendants will be started in turn.")
                    logs = get_service_logs(srv_id)
                    logger.info(f"Systemed service logs for {srv_id}:\n{logs}")
                    child_ids = requests.post(f"{srv_url}/get_children", json={}).json()
                    for child_id in child_ids:
                        time.sleep(1)
                        res = requests.post(f"{srv_url}/start_child", json={"id": child_id}).json()
                        logger.info(f"Service {child_id} is started: {res}")
                        print(f"Service {child_id} is started: {res}")
                else:
                    logger.error(f"Error: failed to start service {srv_id}")
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
            elif op == 'drop':
                logger.info(f"drop {srv_id} ...")
                if sts.get("ActiveState") != "active":
                    logger.warning(f"Warning: service {srv_id} is not running!")
                    print(f"Service {srv_id} is not running!")
                    sys.exit(1)
                child_ids = requests.post("{}://{}:{}/get_children".format("https" if cfg.ssl else "http", cfg.host, cfg.port), json={}).json()
                for child_id in child_ids:
                    requests.post("{}://{}:{}/stop_child".format("https" if cfg.ssl else "http", cfg.host, cfg.port), json={"id":child_id})
                    if not wait_for_service_stopped(child_id, timeout=30):
                        logger.warning(f"Warning: service {child_id} still not fully stopped")
                    print(f"Service {child_id} is stopped.")
                if close_service(srv_id):
                    logs = get_service_logs(srv_id)
                    logger.info(f"Systemed service logs for {srv_id}:\n{logs}")
                    if not wait_for_service_stopped(srv_id, timeout=30):
                        logger.warning(f"Warning: service {srv_id} still not fully stopped, database may be locked")
                    print(f"Service {srv_id} is stopped.")
                    delete_localdb()
                    logger.info(f"Service {srv_id} and its descendants are dropped.")
                    print(f"Service {srv_id} and its descendants are dropped.")
                else:
                    logger.error(f"Error: failed to stop service {srv_id}!")
            else:
                logger.error(f"Error: invalid command {op}")
    else:
        logger.error(f"Error: miss command")
