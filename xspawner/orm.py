import inspect
import re
import os
import shutil
from typing import Optional
from tortoise import Tortoise
from tortoise import fields, models
from urllib.parse import quote_plus, unquote_plus
from xspawner.constants import Config

def caller_module(idx=1):
    # stack[0]    caller_module() 自己
    # stack[1]    init_database()（它调用了 caller_module()）
    # stack[2]    调用 init_database() 的那个函数（最终用户代码）
    caller_frame = inspect.stack()[idx]
    return inspect.getmodule(caller_frame[0])


def make_connection_str(setting: dict) -> str:
    if "category" not in setting:
        raise ValueError(f"Miss category parameter in setting {setting}")
    category = setting["category"]

    conn_str = ""
    if category == "sqlite":
        conn_str = '{}://{}'.format(
            category,
            setting["file"]
            )
    elif category in ("mysql", "postgres"):
        required = ["usr", "psw", "host", "port", "name"]
        missing = [k for k in required if k not in setting]
        if missing:
            raise ValueError(f"Miss parameter: {missing}")
        else:
            conn_str = '{}://{}:{}@{}:{}/{}'.format(
                category,
                quote_plus(setting["usr"]),
                quote_plus(setting["psw"]),
                setting["host"],
                setting["port"],
                quote_plus(setting["name"])
                )
    else:
        raise ValueError(f"Invalid category: {category}")
    return conn_str


def parse_connection_str(conn_str: str) -> dict:
    """
    将 make_connection_str 生成的连接字符串反解析为 settings dict。

    输入示例:
      "sqlite://kv.db"
      "sqlite:///opt/data/kv.db"
      "mysql://user:pass@127.0.0.1:3306/mydb"
      "postgres://user%40corp:p%2Fw@db.local:5432/prod"

    返回:
      {"category": "sqlite", "file": "kv.db"}
      {"category": "mysql", "usr": "user", "psw": "pass",
       "host": "127.0.0.1", "port": 3306, "name": "mydb"}
    """
    if not isinstance(conn_str, str) or "://" not in conn_str:
        raise ValueError(f"Invalid conn_str: {conn_str!r}")

    category, rest = conn_str.split("://", 1)
    category = category.lower()

    # ── sqlite: 剩余部分就是文件路径 ─────────────────────
    if category == "sqlite":
        if not rest:
            raise ValueError(f"Empty sqlite file path: {conn_str!r}")
        return {"category": "sqlite", "file": rest}

    # ── mysql / postgres: usr:psw@host:port/name ─────────
    if category in ("mysql", "postgres"):
        # usr/psw/name 已被 quote_plus 编码, 因此不含未编码的 : @ /
        m = re.fullmatch(
            r"(?P<usr>[^:@/]+):(?P<psw>[^@]*)@"
            r"(?P<host>[^:/]+):(?P<port>\d+)"
            r"/(?P<name>.+)",
            rest,
        )
        if not m:
            raise ValueError(f"Invalid {category} conn_str: {conn_str!r}")
        g = m.groupdict()
        return {
            "category": category,
            "usr":  unquote_plus(g["usr"]),
            "psw":  unquote_plus(g["psw"]),
            "host": g["host"],           # host 未编码, 原样返回
            "port": int(g["port"]),      # 转 int 便于直接用
            "name": unquote_plus(g["name"]),
        }
    raise ValueError(f"Invalid category: {category}")


async def open_database(conn: str, mmod: Optional[str] = None):
    '''
    初始化连接并建表
    (去 orm 和 mmod 模块中查找模型类)
    '''
    await Tortoise.init(
        db_url=conn,
        modules={
            'models': [__name__, mmod] if mmod else [__name__]
        }
    )

    await Tortoise.generate_schemas(safe=True)


async def close_database():
    '''
    关闭所有数据库连接
    '''
    await Tortoise.close_connections()


def _quote_mysql_ident(name: str) -> str:
    """MySQL 标识符转义：反引号包裹，内部反引号翻倍"""
    return "`" + name.replace("`", "``") + "`"


def _quote_pg_ident(name: str) -> str:
    """PostgreSQL 标识符转义：双引号包裹，内部双引号翻倍"""
    return '"' + name.replace('"', '""') + '"'


async def _drop_mysql_database(host, port, usr, psw, name):
    import aiomysql
    # 不指定 db，直接连服务器
    conn = await aiomysql.connect(
        host=host, port=port,
        user=usr, password=psw,
        autocommit=True,
    )
    try:
        async with conn.cursor() as cur:
            # 先杀掉目标库上的其他连接（需要 PROCESS 权限，权限不足就跳过）
            try:
                await cur.execute(
                    "SELECT id FROM information_schema.processlist "
                    "WHERE db = %s AND id <> CONNECTION_ID()",
                    (name,),
                )
                rows = await cur.fetchall()
                for (pid,) in rows:
                    await cur.execute(f"KILL {int(pid)}")
            except Exception:
                pass

            await cur.execute(
                f"DROP DATABASE IF EXISTS {_quote_mysql_ident(name)}"
            )
    finally:
        conn.close()


async def _drop_postgres_database(host, port, usr, psw, name):
    import asyncpg
    # 连到默认的 postgres 管理库
    conn = await asyncpg.connect(
        host=host, port=port,
        user=usr, password=psw,
        database="postgres",
    )
    try:
        # 先终止目标库上的其他连接
        await conn.fetch(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        # 删除数据库
        await conn.execute(
            f"DROP DATABASE IF EXISTS {_quote_pg_ident(name)}"
        )
    finally:
        await conn.close()


def _drop_sqlite_database(file):
    files_to_delete = [file, f"{file}-shm", f"{file}-wal"]
    for fname in files_to_delete:
        if os.path.exists(fname):
            os.remove(fname)


async def drop_database(conn: str):
    setting = parse_connection_str(conn)
    if "category" not in setting:
        raise ValueError(f"Miss category parameter in setting {setting}")
    category = setting["category"]

    if category == "sqlite":
        if "file" not in setting:
            raise ValueError(f"Miss file parameter in setting")
        _drop_sqlite_database(setting["file"])
        return

    if category not in ("mysql", "postgres"):
        raise ValueError(f"Invalid category: {category}")

    required = ["usr", "psw", "host", "port", "name"]
    missing = [k for k in required if k not in setting]
    if missing:
        raise ValueError(f"Miss parameter: {missing}")

    host = setting["host"]
    port = int(setting["port"])
    usr = setting["usr"]
    psw = setting["psw"]
    dbname = setting["name"]

    try:
        await close_database()
    except Exception:
        pass

    if category == "mysql":
        await _drop_mysql_database(host, port, usr, psw, dbname)
    elif category == "postgres":
        await _drop_postgres_database(host, port, usr, psw, dbname)


async def _checkpoint_sqlite_data() -> bool:

    conn_obj = Tortoise.get_connection("default")
    await conn_obj.execute_query("PRAGMA wal_checkpoint(TRUNCATE);")

    return True


def _backup_sqlite_data(file: str, outfile: str = None) -> bool:
    """
    用 sqlite3 CLI 的 .backup 命令备份 SQLite 数据库。

    - .backup 使用 SQLite 的在线备份 API，自动合并 WAL 中的未合并数据
    - 备份期间不阻塞其他连接
    - 目标文件不存在则创建，存在则覆盖

    参数:
      - file:    源数据库路径
      - outfile: 输出文件，默认 f"{file}.bak"

    返回:
      - True  备份成功
      - False 源文件不存在（没什么可备份）
      - 异常  sqlite3 执行失败
    """
    import subprocess

    if not os.path.exists(file):
        return False

    if outfile is None:
        outfile = f"{file}.bak"

    # 通过 stdin 传 .backup，避免命令行的引号/空格解析问题
    result = subprocess.run(
        ["sqlite3", file],
        input=f".backup '{outfile}'\n".encode(),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sqlite3 backup failed: {result.stderr.decode(errors='replace')}"
        )

    return True


def _backup_mysql_data(host, port, usr, psw, name, outfile: str = None) -> bool:
    """
    用 mysqldump 备份 MySQL 数据库。
    需要安装 > sudo apt install mysql-client
    参数:
      - host/port/usr/psw/name: 连接参数
      - outfile: 输出文件，默认 f"{name}.sql.bak"

    返回: True 成功 / 失败抛 RuntimeError
    """
    import subprocess
    import tempfile

    if outfile is None:
        outfile = f"{name}.sql.bak"

    # 密码写进临时选项文件，避免命令行泄露（ps 能看到命令行参数）
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".cnf", delete=False
    ) as f:
        f.write("[client]\n")
        f.write(f"password={psw}\n")
        cnf_path = f.name

    try:
        cmd = [
            "mysqldump",
            f"--defaults-extra-file={cnf_path}",
            "-h", host,
            "-P", str(port),
            "-u", usr,
            "--single-transaction",     # InnoDB 一致性快照，不锁表
            "--routines",               # 存储过程/函数
            "--triggers",               # 触发器
            "--events",                 # 事件调度
            "--set-gtid-purged=OFF",    # 避免 GTID 干扰恢复
            name,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"mysqldump failed: {result.stderr.decode(errors='replace')}"
            )
        with open(outfile, "wb") as f:
            f.write(result.stdout)
    finally:
        try:
            os.unlink(cnf_path)
        except OSError:
            pass

    return True


def _backup_postgres_data(host, port, usr, psw, name, outfile: str = None) -> bool:
    """
    用 pg_dump 备份 PostgreSQL 数据库。
    需要安装 > sudo apt install postgresql-client
    参数:
      - host/port/usr/psw/name: 连接参数
      - outfile: 输出文件，默认 f"{name}.sql.bak"

    返回: True 成功 / 失败抛 RuntimeError
    """
    import subprocess

    if outfile is None:
        outfile = f"{name}.sql.bak"

    # pg_dump 从环境变量 PGPASSWORD 读密码，避免命令行泄露
    env = os.environ.copy()
    env["PGPASSWORD"] = psw

    cmd = [
        "pg_dump",
        "-h", host,
        "-p", str(port),
        "-U", usr,
        "-F", "p",          # 纯文本 SQL 格式
        "-f", outfile,      # 直接写文件
        "--no-owner",       # 不带 owner
        "--no-privileges",  # 不带 GRANT
        name,
    ]
    result = subprocess.run(cmd, env=env, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed: {result.stderr.decode(errors='replace')}"
        )

    return True


# 有键模型
class KeyedModel(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.CharField(max_length=255, pk=True)
        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.id})"
            attrs['__str__'] = auto_str
        return super().__new__(cls, name, bases, attrs)


# 层次模型
class TieredModel(KeyedModel):
    def __new__(cls, name, bases, attrs):

        meta_class = attrs.get("Meta")
        fk_mapping = getattr(meta_class, "fk_mapping", {}) if meta_class else {}

        # acquire app lablel
        app_label = getattr(meta_class, "app", "models") if meta_class else "models"

        # reference parent
        if "parent" not in attrs and "parent" not in fk_mapping:
            attrs['parent'] = fields.ForeignKeyField(
                f"{app_label}.{name}",  # like "models.SpawnedModel"
                null=True,
                on_delete=fields.SET_NULL,
                related_name="+",               # No reverse access
            )

        # handle fields in fk_mapping
        for field_name, related_model in fk_mapping.items():
            if field_name not in attrs:
                attrs[field_name] = fields.ForeignKeyField(
                    related_model,
                    null=True,
                    on_delete=fields.SET_NULL,
                )

        return super().__new__(cls, name, bases, attrs)


# 配置模型
class SpawnedModel(models.Model, metaclass = TieredModel):
    class Meta:
        table = "m_spawn"

    plugin = fields.CharField(max_length=32)
    host = fields.CharField(max_length=32)
    port = fields.IntField()
    access = fields.CharField(max_length=32)
    log = fields.BooleanField()
    severity = fields.CharField(max_length=16)
    ssl = fields.BooleanField()
    certfile = fields.CharField(max_length=255, default="")
    keyfile = fields.CharField(max_length=255, default="")


# 实体模型
class EntityModel(models.Model):
    class Meta:
        table = "m_entity"
        unique_together = (("spawn", "key"),)           # 联合唯一约束

    id = fields.IntField(pk=True, generated=True)       # 代理主键
    key = fields.CharField(max_length=255)              # 业务键
    spawn = fields.ForeignKeyField(
        "models.SpawnedModel",
        null=False,
        on_delete=fields.CASCADE,
        related_name="entities",
    )
    value = fields.JSONField(null=True)

    def __str__(self):
        return f"{self.__class__.__name__}({self.key}@{self.spawn_id})"


def config_model_to_tuple(model: SpawnedModel) -> Config:
    if not isinstance(model, SpawnedModel):
        raise TypeError(f"Expected SpawnedModel instance, got {type(model)}")
    return Config(
        id = model.id,
        plugin = model.plugin,
        host = model.host,
        port = model.port,
        access = model.access,
        log = model.log,
        severity = model.severity,
        ssl = model.ssl,
        certfile = model.certfile,
        keyfile = model.keyfile,
        parent = model.parent_id or ""
    )

def config_model_to_dict(model: SpawnedModel) -> dict:
    return config_model_to_tuple(model)._asdict()