# XSpawner Project

**XSpawner** is a lightweight Python web management platform designed to help developers rapidly build small yet fully-featured asynchronous web services. It combines [**Tornado**](https://www.tornadoweb.org/)'s high-performance asynchronous capabilities with [**PyWebIO**](https://pywebio.readthedocs.io)'s rapid UI components, significantly simplifying the development of modern web applications.


## Key Features

XSpawner provides the following key features:

- **🐍 Full Python Stack**: Both front-end and back-end are developed in pure Python, with no need to switch languages or frameworks.
- **📦 Class as a Service (CaaS)**: Each service is encapsulated as an independent class, making it easy to organize, reuse, and manage.
- **🌐 Simple Lifecycle Management**: Manage service creation, startup, and shutdown through dependency relationships.
- **⚡ Asynchronous Programming**: Powered by Tornado's battle-tested coroutines for high-performance asynchronous processing.
- **🔒 Security Enhancement**: Native support for SSL/TLS certificates to ensure secure communication.
- **🗄️ Database Support**: Powered by Tortoise ORM, supports SQLite, MySQL, and PostgreSQL, with built-in database backup and cleanup features.

## Use Cases

XSpawner is suitable for the following scenarios:

- **Rapid Prototyping**: Quickly build an asynchronous web service with a UI to validate ideas.
- **Small Management Platforms**: Build lightweight admin tools or internal services.
- **Microservices / Service Orchestration**: Leverage "Class as a Service" and lifecycle management to organize multiple independent services.
- **Asynchronous Task Processing**: Scenarios requiring high-concurrency or asynchronous task handling using Tornado coroutines.


## Docker Deployment

The repository includes one generic Dockerfile for **administrator service clusters**. A cluster is one root administrator plus all services it manages, and runs in a single container. The image is built once and reused by every cluster; the domain/IP, ports and cluster id are passed as runtime environment variables. Docker Compose is not required.

The image uses the Ubuntu 20.04 base system and Ubuntu's default Python 3.8.

```bash
chmod +x deploy/*.sh
./deploy/build.sh
./deploy/cluster.sh example-com example.com 8000

# 在线备份 / 删除集群
./deploy/backup.sh example-com
./deploy/remove.sh example-com          # 保留数据卷
./deploy/remove.sh example-com --purge  # 同时删除数据卷和插件卷
```

See [DOCKER.md](DOCKER.md) for the container model, runtime variables, volumes, optional Caddy setup, backup/update procedures and security notes.

The legacy bare-metal/systemd scripts are archived in [`legacy/baremetal/`](legacy/baremetal/README.md); they are not used by the container deployment.


## Project Information

- **License**: Apache-2.0
- **Author**: Song Feng
- **Contact**: 43217183@qq.com
- **Repository**: https://github.com/franksongfeng/xspawner


## Summary

XSpawner is a lightweight web management platform for Python developers. Through the combination of "Full Python Stack + Class as a Service + Asynchronous Programming", it lowers the barrier to building asynchronous web services. It is ideal for scenarios requiring rapid development, deployment, and management of small asynchronous web services, especially for developers familiar with Python who want to avoid the complexity of front-end/back-end separation.

