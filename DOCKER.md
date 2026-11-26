# Docker 容器化部署

## 1. 架构

容器边界是 **administrator 服务集群**：

```
宿主机
├── xspawner-caddy                     可选：共享 TLS / 反向代理
├── xspawner-example-com-admin         example.com 集群
│   └── 根 administrator + 全部普通服务 / 子 administrator
├── xspawner-10-0-0-5-admin            10.0.0.5 集群
└── ...
```

- **一个集群 = 一个容器**。根 administrator 和它管理的所有服务都在同一个容器内，由容器里的 systemd 管理。
- **所有集群共用同一个镜像**。镜像里不写入域名、端口等集群参数；这些参数在 `docker run` 时通过环境变量传入。
- 全部用 `docker build` / `docker run` 完成，不使用 Docker Compose。
- 基础系统固定为 **Ubuntu 20.04**，Python 使用 Ubuntu 默认的 **Python 3.8**。
- 集群之间通过独立的容器名、数据卷和插件卷隔离。
- Caddy 是独立容器，可选；启用时通过 Admin API 把 `/services/<id>/*` 路由到对应服务。

镜像内包含：Ubuntu 20.04、Python 3.8、systemd、sqlite3、curl。不包含 Docker CLI，也不需要挂载 `/var/run/docker.sock`。

## 2. 部署文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 所有集群共用的通用镜像 |
| `docker/install-deps.py` | 从 `pyproject.toml` 安装运行时 Python 依赖 |
| `docker/entrypoint.sh` | 生成 administrator 的 systemd unit，设置集群级环境，最后 `exec systemd` |
| **`deploy/build.sh`** | 构建通用镜像 `xspawner:latest` |
| **`deploy/cluster.sh`** | 部署一个集群：准备卷、选项、Caddy 站点并启动容器 |
| `deploy/caddy.sh` | 启动共享 Caddy 容器（可选） |
| `deploy/sync-caddy.sh` | reload Caddy，并重启已有集群容器以恢复动态路由 |
| `deploy/backup.sh` | 在线备份集群 SQLite 数据库和插件目录，并复制到宿主机 |
| `deploy/remove.sh` | 删除集群容器和 Caddy 站点；`--purge` 同时删除数据卷 |
| `deploy/Caddyfile` | Caddy 主配置，导入 `deploy/caddy-sites/*.caddy` |
| `deploy/caddy-sites/*.caddy` | 每个集群的站点块 |

> 本项目只支持容器部署，不提供裸机/systemd 脚本，也不提供根目录兼容 shim。运维统一使用 `deploy/*.sh` 和 `docker` 命令。

## 3. 快速开始（单集群）

前置条件：Docker Engine 20.10+；启用 Caddy 时宿主机 80/443 必须空闲，所有集群的 root 端口不能重复，普通服务端口也要避免冲突。

```bash
chmod +x deploy/*.sh

# 1) 构建一次通用镜像
./deploy/build.sh

# 2) 部署 example.com 集群
./deploy/cluster.sh example-com example.com 8000
```

访问：

```
https://example.com/services/admin/
```

`deploy/caddy-sites/example-com.caddy` 默认使用 `tls internal`（Caddy 内部 CA），浏览器会提示证书不受信任。生产环境请把 `tls internal` 换成 `tls <cert> <key>`，或删除 `deploy/Caddyfile` 里的 `auto_https off` 后交给 Caddy 自动申请 ACME 证书。

实际等价于：

```bash
docker build --pull -t xspawner:latest .

docker run -d --name xspawner-example-com-admin \
    --label xspawner.managed=true \
    --label xspawner.cluster=example-com \
    --label xspawner.role=root \
    --network host --privileged --cgroupns=host \
    --stop-signal SIGRTMIN+3 --stop-timeout 30 --restart unless-stopped \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp \
    -e XSPAWNER_CLUSTER=example-com \
    -e XSPAWNER_ID=admin \
    -e XSPAWNER_PLUGIN=administrator \
    -e XSPAWNER_HOST=example.com \
    -e XSPAWNER_PORT=8000 \
    -e XSPAWNER_ACCESS=127.0.0.1 \
    -e XSPAWNER_SSL=true \
    -e XSPAWNER_LOG=true \
    -e XSPAWNER_SEVERITY=info \
    -e XSPAWNER_DATA_DIR=/data \
    -v xspawner-example-com-plugins:/opt/xspawner/xspawner/plugins \
    -v xspawner-example-com-data:/data \
    xspawner:latest
```

上面只列出 xspawner 容器本身；Caddy 的启动、站点写入和动态路由恢复由 `deploy/cluster.sh` 完成。

### 使用宿主机已有 Caddy

如果宿主机已经用 systemd 运行 Caddy，并且已经配置好 `https://xspawner.com` 和真实证书，就不要启动 `xspawner-caddy` 容器，否则会抢占 80/443/2019：

```bash
XSPAWNER_MANAGE_CADDY=false \
    ./deploy/cluster.sh xspawner-com xspawner.com 8000
```

前提：

- 宿主机 Caddy 正在运行，Admin API 为 `127.0.0.1:2019`
- 宿主机 Caddy 已有 `https://xspawner.com` 站点
- 宿主机上的旧 xspawner 服务已停止，8000 端口空闲

此时 xspawner 容器只负责业务进程，启动时会向宿主机 Caddy 注入 `/services/<id>/*` 路由。重启或 reload 宿主机 Caddy 后，动态路由会丢失，需要重启容器恢复：

```bash
systemctl reload caddy
docker restart xspawner-xspawner-com-admin
```

### 国内网络 / Docker Hub 不可达

如果 `docker build` 在 `FROM ubuntu:20.04` 处报 `request canceled while waiting for connection`，这是宿主机 Docker daemon 无法访问 Docker Hub，不是 Dockerfile 的问题。先配置镜像加速器（阿里云控制台：容器镜像服务 → 镜像工具 → 镜像加速器），然后重启 Docker：

```
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": ["https://<你的加速器ID>.mirror.aliyuncs.com"]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

> 一个可用的加速器地址：https://vt6e27zj.mirror.aliyuncs.com

拉取基础镜像：

```bash
docker pull ubuntu:20.04
```

> 如果 `/etc/docker/daemon.json` 已存在，请只把 `registry-mirrors` 合并进去，不要覆盖其他配置。

基础镜像拉取成功后，接下来构建 `xspawner:latest`。

```bash
cd /opt/xspawner

APT_MIRROR=http://mirrors.aliyun.com/ubuntu \
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
./deploy/build.sh
```

> `build.sh` 会先检查本地已有 `ubuntu:20.04`，不会再次访问 Docker Hub。
>
> 构建过程中如果出现 `legacy builder is deprecated`，可以忽略。

确认镜像：

```bash
docker images | grep xspawner
```

应看到：

```bash
xspawner    latest    ...
```

## 4. 多域名 / 多 IP

```bash
./deploy/cluster.sh example-com example.com 8000
./deploy/cluster.sh 10-0-0-5    10.0.0.5    9000
```

每个集群使用：

| 资源 | 命名 |
|---|---|
| 镜像 | `xspawner:latest`（共享） |
| 容器 | `xspawner-<cluster>-admin` |
| 数据卷 | `xspawner-<cluster>-data` |
| 插件卷 | `xspawner-<cluster>-plugins` |
| Caddy 站点 | `deploy/caddy-sites/<cluster>.caddy` |

所有容器使用 host 网络，因此**不同集群的 root 端口不能重复；普通服务端口也需人工规划，避免冲突**。

> Caddy 路由 `@id` 使用 `host-id` 前缀，多域名共享同一个 Caddy 时不会互相覆盖；同一域名下的服务 id 保持唯一即可。

`deploy/cluster.sh` 在启动新集群前会：

1. 启动或复用共享 Caddy；
2. 写入本集群站点块；
3. `sync-caddy.sh` 重新加载 Caddy，并重启已有集群容器，让它们重新注册动态路由；
4. 最后启动新集群容器。

> Caddy reload 会丢弃通过 Admin API 注入的动态路由，所以新增/修改站点时已有集群会有短暂中断。

## 5. 不使用 Caddy / 直接暴露端口

```bash
XSPAWNER_SSL=false ./deploy/cluster.sh example-com example.com 8000
```

此时不启动共享 Caddy，访问：

```
http://example.com:8000/services/admin/
```

适合内网、端口映射或前面已有负载均衡/反向代理的场景。切换为 `false` 时，需要手动删除旧的 `deploy/caddy-sites/<cluster>.caddy` 并执行 `./deploy/sync-caddy.sh`。

## 6. 环境变量

镜像不写入集群配置，以下变量全部在运行时传入：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `XSPAWNER_CLUSTER` | `default` | 集群标识，用于容器/卷命名、数据库文件名（`<cluster>.db`）和日志文件名（`<cluster>.log`） |
| `XSPAWNER_ID` | `admin` | 根 administrator 实例标识 |
| `XSPAWNER_PLUGIN` | `administrator` | 根实例插件 |
| `XSPAWNER_HOST` | `127.0.0.1` | 对外域名或 IP |
| `XSPAWNER_PORT` | `8000` | 根 administrator 监听端口 |
| `XSPAWNER_ACCESS` | `0.0.0.0` | 服务监听地址；cluster.sh 启用 Caddy 时默认传 `127.0.0.1` |
| `XSPAWNER_SSL` | `false` | 是否启用 TLS；`true` 且无证书时走 Caddy |
| `XSPAWNER_LOG` | `false` | 是否启用日志 |
| `XSPAWNER_SEVERITY` | `info` | 日志级别 |
| `XSPAWNER_DATA_DIR` | `/data` | 数据库、日志、备份所在目录 |
| `XSPAWNER_LIBS` | 空 | 启动时额外安装的 pip 包，逗号分隔 |
| `XSPAWNER_ADDITIONAL` | 空 | 原样传给根 administrator 的附加字符串 |
| `XSPAWNER_CERTFILE/KEYFILE` | 空 | 直接使用证书文件时的容器内路径 |

> `deploy/cluster.sh` 默认传入 `XSPAWNER_SSL=true` 和 `XSPAWNER_LOG=true`；需要明文 HTTP 时用 `XSPAWNER_SSL=false`。启用 Caddy 时脚本默认让服务只监听 `127.0.0.1`，由 Caddy 统一对外；不使用 Caddy 时默认监听 `0.0.0.0`。
>
> `XSPAWNER_LIBS` 适合安装带 wheel 的包；需要本地编译的包请在自定义镜像中补 `build-essential`、`python3-dev` 等构建依赖。

## 7. 持久化与备份

| 卷 | 容器内路径 | 内容 |
|---|---|---|
| `xspawner-<cluster>-data` | `/data` | SQLite 数据库、日志、备份 |
| `xspawner-<cluster>-plugins` | `/opt/xspawner/xspawner/plugins` | 上传/安装的插件 |
| `xspawner-<cluster>-certs`（可选） | `/opt/xspawner/certs` | 自有证书 |

SQLite 数据库文件名为 `/data/<cluster>.db`，例如 `xspawner-com` 集群使用 `/data/xspawner-com.db`；日志文件为 `/data/<cluster>.log`。

命名卷第一次挂载时会由镜像内容初始化插件目录。如果改成 bind mount，需要先把镜像里的 `xspawner/plugins` 复制到宿主机目录。镜像升级后，已存在的插件卷不会自动覆盖内置插件；需要更新内置插件时应备份后重建插件卷。

在线备份 SQLite 和插件目录（推荐，运行中也可用）：

```bash
./deploy/backup.sh example-com
./deploy/backup.sh example-com ./backups
```

`deploy/backup.sh` 在容器内用 `sqlite3` 的 `.backup` 做一致性备份，并打包插件目录，再把两个文件复制到宿主机的 `backups/` 目录。备份文件同时保留在数据卷的 `/data/backups/` 中。

冷备整个数据卷（容器停止时更安全；插件卷需要另外打包）：

```bash
docker run --rm \
    -v xspawner-example-com-data:/data \
    -v "$PWD":/backup \
    alpine tar czf "/backup/xspawner-example-com-$(date +%F).tgz" -C /data .
```

## 8. 运维

```bash
# 查看集群容器
docker ps --filter label=xspawner.managed=true

# 查看容器/PID 1
docker logs -f xspawner-example-com-admin
docker exec xspawner-example-com-admin ps -p 1 -o comm=

# 进入容器并管理 systemd 服务（unit 名就是服务 id）
docker exec -it xspawner-example-com-admin bash
systemctl status admin
systemctl list-units --type=service

# 重启单个集群（保留卷）
docker restart xspawner-example-com-admin

# 更新镜像后重建集群（数据卷保留）
./deploy/build.sh
./deploy/cluster.sh example-com example.com 8000

# 删除集群（默认保留数据卷和插件卷）
./deploy/remove.sh example-com

# 删除集群并清除数据卷/插件卷（不可恢复，先备份）
./deploy/remove.sh example-com --purge

# 只停容器、保留卷
docker stop xspawner-example-com-admin
```

## 9. 安全与限制

- **systemd 需要特权**：容器以 `--privileged --cgroupns=host` 运行，systemd 才能作为 PID 1 管理 unit，等价于宿主机 root。只运行可信插件和代码。
- **不再挂载 Docker socket**：集群内的 administrator 子服务也是同一个容器里的 systemd 服务，不再需要 Docker CLI 或 `/var/run/docker.sock`。
- **host 网络**：所有集群共享宿主机网络，root 端口和普通服务端口都必须人工规划，不能冲突。
- **回环监听**：启用 Caddy 时 `cluster.sh` 默认让 xspawner 服务只监听 `127.0.0.1`，外部只能经 Caddy 访问；不使用 Caddy 时才会监听 `0.0.0.0`。
- **Caddy 单点**：所有启用 TLS 的集群共享 `xspawner-caddy`；Caddy 重启或 reload 后需要重启集群容器恢复动态路由。
- **Python 3.8 已 EOL**：Ubuntu 20.04 默认 Python 3.8 已停止安全更新，镜像固定了 3.8 上可用的依赖版本。本方案遵循 Ubuntu 20.04 + 默认 Python 的约束；新部署建议改用更新基础镜像。
- **SQLite 单文件**：同一集群共享 `/data/<cluster>.db`；不同集群的数据卷相互隔离。
