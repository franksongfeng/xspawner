import inspect
from tortoise import Tortoise
from tortoise import fields, models
from urllib.parse import quote_plus
from xspawner.constants import Config

def model_module_name(this=True):
    if this:
        return __name__
    else:
        caller_frame = inspect.stack()[1]  # 索引0是当前函数，索引1是调用者
        return inspect.getmodule(caller_frame[0]).name


async def open_database(category, **setting):
    '''
    根据 category（sqlite / mysql / postgres）构建连接字符串
    '''
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
    '''
    初始化连接并建表
    (去 orm 模块中查找模型类)
    '''
    await Tortoise.init(
        db_url=conn_str,
        modules={
            'models': [model_module_name(True)]
        }
    )

    await Tortoise.generate_schemas(safe=True)


async def close_database():
    '''
    关闭所有数据库连接
    '''
    await Tortoise.close_connections()

# 键值数据
class KVModel(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.CharField(max_length=255, pk=True)
        if "data" not in attrs:
            attrs['data'] = fields.JSONField(null=True)   # 可存 dict, list, str, int, bool, None
        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.id})"
            attrs['__str__'] = auto_str
        return super().__new__(cls, name, bases, attrs)


# 层次数据
class TieredModel(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.CharField(max_length=255, pk=True)
        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.name})"
            attrs['__str__'] = auto_str

        meta_class = attrs.get("Meta")
        fk_mapping = getattr(meta_class, "fk_mapping", {}) if meta_class else {}

        # acquire app lablel
        app_label = getattr(meta_class, "app", "models") if meta_class else "models"

        # reference parent
        if "parent" not in attrs and "parent" not in fk_mapping:
            attrs['parent'] = fields.ForeignKeyField(
                f"{app_label}.{name}",  # like "models.ConfigModel"
                null=True,
                on_delete=fields.SET_NULL,
                related_name="children",
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

# 配置数据
class ConfigModel(models.Model, metaclass = TieredModel):
    class Meta:
        table = "m_config"

    plugin = fields.CharField(max_length=32)
    host = fields.CharField(max_length=32)
    port = fields.IntField()
    access = fields.CharField(max_length=32)
    log = fields.BooleanField()
    severity = fields.CharField(max_length=16)
    ssl = fields.BooleanField()
    certfile = fields.CharField(max_length=255, default="")
    keyfile = fields.CharField(max_length=255, default="")


def config_model_to_tuple(model: ConfigModel) -> Config:
    if not isinstance(model, ConfigModel):
        raise TypeError(f"Expected ConfigModel instance, got {type(model)}")
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

def config_model_to_dict(model: ConfigModel) -> dict:
    return config_model_to_tuple(model)._asdict()