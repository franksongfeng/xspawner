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

- **一个集群 = 一个容器**。根 administrator 和它管理的所有服务都在同一个容器内，由容器里的 systemd 管理。根 administrator 对子服务采取“静态停止绑定 + 运行时启动编排”的管理方式。
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
| **`deploy/build.sh`** | 构建通用镜像 `xspawner:latest`；`XSPAWNER_PUSH=true` 时构建后自动推送 |
| **`deploy/push.sh`** | 把本地镜像推送到指定镜像仓库（多标签、`--all-tags`、自动登录） |
| **`deploy/pull.sh`** | 从镜像仓库拉取镜像，默认重命名为 `xspawner:latest`，支持按摘要拉取 |
| `deploy/lib-image.sh` | `push.sh` / `pull.sh` 共用的镜像引用解析、校验和 `docker login` 函数 |
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

> 如果镜像已经发布到镜像仓库，可以跳过构建，用 `./deploy/pull.sh <registry>/<namespace>/xspawner:<tag>` 拉到本机再部署，见第 4 节。

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

如果宿主机已经用 systemd 运行 Caddy，并且已经配置好 `https://example.com` 和真实证书，就不要启动 `xspawner-caddy` 容器，否则会抢占 80/443/2019：

```bash
XSPAWNER_MANAGE_CADDY=false \
    ./deploy/cluster.sh example-com example.com 8000
```

前提：

- 宿主机 Caddy 正在运行，Admin API 为 `127.0.0.1:2019`
- 宿主机 Caddy 已有 `https://example.com` 站点
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

## 4. 镜像仓库：推送与拉取

镜像在开发机构建一次，通过镜像仓库分发到各台部署机，部署机就不必安装构建依赖。`deploy/push.sh` 和 `deploy/pull.sh` 只负责打标签、登录和传输，不改变“所有集群共用一个镜像”的模型。

### 4.1 约定

- 镜像引用统一写作 `<registry>/<namespace>/<repo>:<tag>`，也可以用摘要固定版本：`...@sha256:<64位十六进制>`。
- 本地镜像名由 `XSPAWNER_IMAGE` 指定，默认 `xspawner:latest`；`cluster.sh` 按这个名字启动容器。
- 仓库地址必须**全小写**且**带命名空间**。只写 `xspawner` 会被 Docker 展开成 `docker.io/library/xspawner`，脚本会直接拒绝，避免误推/误拉官方镜像。
- 凭据只从环境变量或文件读取，不写进命令行参数，避免落进 shell 历史。
- 不写 registry 时按 Docker Hub（`docker.io`）处理。

### 4.2 推送到镜像仓库

```bash
./deploy/build.sh

# 显式给仓库和标签
./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3 latest

# 或者用环境变量固定仓库，位置参数只当标签
export XSPAWNER_REPO=registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner
./deploy/push.sh 1.2.3
```

推送前需要登录。交互式登录最省事，密码不会进 shell 历史：

```bash
docker login registry.cn-hangzhou.aliyuncs.com
```

CI 里可以交给脚本自动登录：

```bash
XSPAWNER_REGISTRY_USER=my-name \
XSPAWNER_REGISTRY_PASSWORD="$ACR_TOKEN" \
./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3
```

密码不想放进环境变量时改用文件（脚本会去掉文件末尾的换行和 CR）：

```bash
XSPAWNER_REGISTRY_USER=my-name \
XSPAWNER_REGISTRY_PASSWORD_FILE=/run/secrets/acr-password \
./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3
```

构建完直接推送：

```bash
XSPAWNER_PUSH=true \
XSPAWNER_REPO=registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner \
XSPAWNER_TAG=1.2.3 \
./deploy/build.sh
```

推送某仓库下的全部标签：

```bash
./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner --all-tags
```

推送成功后会打印每个标签的 `digest` 和对应的拉取命令。本地 `xspawner:latest` 不会被改名，只是额外打上了仓库标签。

### 4.3 从镜像仓库拉取

```bash
# 拉取并同时打上 xspawner:latest（默认行为）
./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3

# 只拉取，不改本地标签
./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3 --no-retag

# 按摘要拉取：标签以后被覆盖也不会拉错版本
./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner@sha256:<64位>

# 存成另一个本地名
XSPAWNER_IMAGE=xspawner:1.2.3 ./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3
```

`pull.sh` 会核对拉取到的摘要与请求的摘要一致，并输出 image id。默认把拉下来的镜像重命名为 `xspawner:latest`，之后直接 `./deploy/cluster.sh ...` 即可。

也可以把“拉取”交给 `cluster.sh`，部署机不用手工执行 `pull.sh`：

```bash
# 本地没有该镜像时从仓库拉取
XSPAWNER_IMAGE=registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3 \
XSPAWNER_PULL=true \
./deploy/cluster.sh example-com example.com 8000

# 每次部署都拉取，用于仓库里镜像已更新的滚动升级
XSPAWNER_IMAGE=registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3 \
XSPAWNER_PULL=always \
./deploy/cluster.sh example-com example.com 8000
```

`XSPAWNER_PULL` 的三种取值：

| 值 | 行为 |
|---|---|
| `false`（默认） | 本地没有镜像时执行 `./deploy/build.sh` |
| `true` | 本地没有镜像时从 `XSPAWNER_IMAGE` 指向的仓库拉取 |
| `always` | 每次部署都先拉取 |

> 打开 `XSPAWNER_PULL` 后必须把 `XSPAWNER_IMAGE` 设成带命名空间的仓库引用，例如 `registry.example.com/my-ns/xspawner:1.2.3`；仍用默认的 `xspawner:latest` 会直接报错。

### 4.4 各仓库地址示例

| 仓库 | 地址写法 | 说明 |
|---|---|---|
| 阿里云 ACR（个人版） | `registry.cn-hangzhou.aliyuncs.com/<命名空间>/xspawner` | 先在控制台创建命名空间 |
| 阿里云 ACR（企业版） | `<实例名>-<地域>.cr.aliyuncs.com/<命名空间>/xspawner` | 使用企业版实例的访问域名 |
| Docker Hub | `docker.io/<用户名>/xspawner` 或 `<用户名>/xspawner` | 不写 registry 时自动按 Docker Hub 处理 |
| 自建 registry（HTTPS） | `registry.example.com/<命名空间>/xspawner` | 证书需被宿主机 Docker 信任 |
| 自建 registry（HTTP） | `192.0.2.10:5000/<命名空间>/xspawner` | 需要在 daemon.json 配置 `insecure-registries` |

自建 HTTP 仓库需要额外配置（改完重启 Docker）：

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "insecure-registries": ["192.0.2.10:5000"]
}
EOF
sudo systemctl restart docker
```

### 4.5 常见错误

| 报错 | 原因 | 处理 |
|---|---|---|
| `unauthorized: authentication required` | 没登录或凭据过期 | `docker login <registry>`，或设置 `XSPAWNER_REGISTRY_USER` / `XSPAWNER_REGISTRY_PASSWORD` |
| `denied: requested access to the resource is denied` | 命名空间/仓库不存在，或账号没有写权限 | 在控制台先创建命名空间和仓库，确认账号权限 |
| `http: server gave HTTP response to HTTPS client` | 私有仓库只提供 HTTP | 把仓库地址加入 `insecure-registries` |
| `x509: certificate signed by unknown authority` | 仓库使用自签证书 | 把 CA 放到 `/etc/docker/certs.d/<registry>/ca.crt` 并重启 Docker |
| `manifest unknown` / `not found` | 标签不存在（还没推成功，或 tag 写错） | `docker manifest inspect <repo>:<tag>` 核对 |
| `toomanyrequests` | Docker Hub 匿名拉取限流 | 登录后拉取，或改用私有仓库/加速器 |

### 4.6 注意事项

- `docker login` 默认把凭据以 base64 存在 `~/.docker/config.json`；共用机器建议配置 credential helper，用完执行 `docker logout <registry>`。
- 推送会额外生成仓库标签（如 `registry.../xspawner:1.2.3`），不会覆盖本地 `xspawner:latest`。同一个标签重复推送会覆盖仓库里的旧镜像，生产环境建议同时打版本号/日期这类不可变标签，并用摘要部署。
- 镜像里不含域名、端口等集群参数，同一个镜像推到仓库后可被任意数量的集群复用。

## 5. 多域名 / 多 IP

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

## 6. 不使用 Caddy / 直接暴露端口

```bash
XSPAWNER_SSL=false ./deploy/cluster.sh example-com example.com 8000
```

此时不启动共享 Caddy，服务直接监听该端口，UI 在根路径（没有 `/services/<id>/` 前缀）：

```
http://example.com:8000/
```

适合内网、端口映射或前面已有负载均衡/反向代理的场景。切换为 `false` 时，需要手动删除旧的 `deploy/caddy-sites/<cluster>.caddy` 并执行 `./deploy/sync-caddy.sh`。

## 7. 环境变量

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
| `XSPAWNER_CHILD_REAP_INTERVAL` | `60` | administrator 子服务巡检间隔（秒）；`0` 关闭巡检 |
| `XSPAWNER_CHILD_DEAD_THRESHOLD` | `3` | 连续探测失败多少次后自动清理该子服务 |
| `XSPAWNER_CHILD_PROBE_TIMEOUT` | `5` | 单次探测 `/get_info` 的超时（秒） |
| `XSPAWNER_SHUTDOWN_GRACE` | `2` | 收到 SIGTERM 后等待在途请求完成的秒数 |

> `deploy/cluster.sh` 默认传入 `XSPAWNER_SSL=true` 和 `XSPAWNER_LOG=true`；需要明文 HTTP 时用 `XSPAWNER_SSL=false`。启用 Caddy 时脚本默认让服务只监听 `127.0.0.1`，由 Caddy 统一对外；不使用 Caddy 时默认监听 `0.0.0.0`。
>
> `XSPAWNER_LIBS` 适合安装带 wheel 的包；需要本地编译的包请在自定义镜像中补 `build-essential`、`python3-dev` 等构建依赖。

镜像仓库相关变量（只影响 `build.sh` / `push.sh` / `pull.sh` / `cluster.sh`，不会传进容器）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `XSPAWNER_IMAGE` | `xspawner:latest` | 本地镜像名；`XSPAWNER_PULL` 打开时需为仓库引用 |
| `XSPAWNER_PULL` | `false` | `true`：本地无镜像时从仓库拉取；`always`：每次部署都拉取 |
| `XSPAWNER_REPO` | 空 | 镜像仓库地址，供 `push.sh` / `pull.sh` 默认使用 |
| `XSPAWNER_TAG` | 空 | 推送/拉取标签，省略时用本地镜像标签，再退回 `latest` |
| `XSPAWNER_PUSH` | `false` | `build.sh` 构建完成后自动推送，需要同时设置 `XSPAWNER_REPO` |
| `XSPAWNER_REGISTRY_USER` | 空 | 仓库用户名；设置后 `push.sh` / `pull.sh` 自动 `docker login` |
| `XSPAWNER_REGISTRY_PASSWORD` | 空 | 仓库密码或访问令牌 |
| `XSPAWNER_REGISTRY_PASSWORD_FILE` | 空 | 从文件读取密码（CI 推荐，避免密码进环境变量） |
| `XSPAWNER_NO_LOGIN` | `false` | `true` 时跳过自动登录，使用本机已有 docker 凭据 |

## 8. 持久化与备份

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

## 9. 运维

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

# 推送镜像到仓库 / 从仓库拉取镜像
./deploy/push.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner 1.2.3
./deploy/pull.sh registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3

# 查看镜像摘要（推送/拉取时都会打印，也可事后查询）
docker image inspect --format '{{json .RepoDigests}}' registry.cn-hangzhou.aliyuncs.com/my-ns/xspawner:1.2.3

# 删除集群（默认保留数据卷和插件卷）
./deploy/remove.sh example-com

# 删除集群并清除数据卷/插件卷（不可恢复，先备份）
./deploy/remove.sh example-com --purge

# 只停容器、保留卷
docker stop xspawner-example-com-admin
```

### 子服务热升级（systemd socket activation）

每个服务的监听端口由 systemd 的 `<id>.socket` 持有，服务进程通过 `LISTEN_FDS` 继承，不再自己 bind。因此 `systemctl restart <id>` 或调用 administrator 的 `/upgrade_child` 时，监听 socket 不会关闭：重启窗口内的新连接进入内核队列，等新进程接管，不会出现 connection refused。

```bash
# 1) 先更新插件文件（/upload_plugin，或直接替换插件目录里的文件）
# 2) 触发热升级
curl -sS -X POST -H 'Content-Type: application/json' \
    -d '{"id":"hello"}' http://127.0.0.1:8000/upgrade_child
# => {"id":"hello","pid":123,"addr":"http://localhost:8935"}
```

- 旧进程收到 SIGTERM 后停止 accept，等待 `XSPAWNER_SHUTDOWN_GRACE`（默认 2 秒）排空在途请求后退出；长请求可调大（需小于 unit 的 `TimeoutStopSec=30`）。
- 接口会等到新进程 PID 接管后才返回，不会把仍在排空的旧进程 PID 当成成功。
- 新建服务会自动生成 `<id>.socket`；旧版本直接 bind 端口的服务首次升级会自动迁移到 socket activation（这一次会有短暂中断）。
- 确认端口归属：`ss -ltnp | grep :8935` 应同时看到 systemd（PID 1）和 python 进程。

## 10. 安全与限制

- **systemd 需要特权**：容器以 `--privileged --cgroupns=host` 运行，systemd 才能作为 PID 1 管理 unit，等价于宿主机 root。只运行可信插件和代码。
- **不再挂载 Docker socket**：集群内的 administrator 子服务也是同一个容器里的 systemd 服务，不再需要 Docker CLI 或 `/var/run/docker.sock`。
- **仓库凭据**：`push.sh` / `pull.sh` 只从环境变量或密码文件读取凭据，不写命令行；`docker login` 会把凭据以 base64 存进 `~/.docker/config.json`，共用机器上建议配置 credential helper，或用完执行 `docker logout <registry>`。
- **host 网络**：所有集群共享宿主机网络，root 端口和普通服务端口都必须人工规划，不能冲突。
- **回环监听**：启用 Caddy 时 `cluster.sh` 默认让 xspawner 服务只监听 `127.0.0.1`，外部只能经 Caddy 访问；不使用 Caddy 时才会监听 `0.0.0.0`。
- **Caddy 单点**：所有启用 TLS 的集群共享 `xspawner-caddy`；Caddy 重启或 reload 后需要重启集群容器恢复动态路由。
- **Python 3.8 已 EOL**：Ubuntu 20.04 默认 Python 3.8 已停止安全更新，镜像固定了 3.8 上可用的依赖版本。本方案遵循 Ubuntu 20.04 + 默认 Python 的约束；新部署建议改用更新基础镜像。
- **SQLite 单文件**：同一集群共享 `/data/<cluster>.db`；不同集群的数据卷相互隔离。
