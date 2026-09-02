import inspect
from tortoise import Tortoise
from tortoise import fields, models
from urllib.parse import quote_plus


def model_module_name(this=True):
    if this:
        return __name__
    else:
        caller_frame = inspect.stack()[1]  # 索引0是当前函数，索引1是调用者
        return inspect.getmodule(caller_frame[0]).name


async def init_database(category, **setting):
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
            raise ValueError(f"缺少参数: {missing}")
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
        raise ValueError(f"非法类型: {category}")
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

# 半结构化数据
class DynamicObject(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.IntField(pk=True, generated=True)
        if "data" not in attrs:
            attrs['data'] = fields.JSONField(null=True)   # 可存 dict, list, str, int, bool, None
        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.id})"
            attrs['__str__'] = auto_str
        return super().__new__(cls, name, bases, attrs)


# 层级数据
class StaticObject(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "name" not in attrs:
            attrs['name'] = fields.CharField(max_length=255, pk=True)
        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.name})"
            attrs['__str__'] = auto_str

        meta_class = attrs.get("Meta")
        if meta_class:
            fk_mapping = getattr(meta_class, "fk_mapping", {})
            if fk_mapping:
                for field_name, related_model in fk_mapping.items():
                    if field_name not in attrs:
                        attrs[field_name] = fields.ForeignKeyField(related_model, null=True, on_delete=fields.SET_NULL)

        return super().__new__(cls, name, bases, attrs)


class Configurations(models.Model, metaclass = StaticObject):
    class Meta:
        table = "configurations"
        fk_mapping = {
            "parent": "Configurations"
        }
    plugin = fields.CharField(max_length=32)
    host = fields.CharField(max_length=32)
    port = fields.IntField()
    access = fields.CharField(max_length=32)

    reportup = fields.BooleanField()
    log = fields.BooleanField()
    severity = fields.CharField(max_length=16)
    ssl = fields.BooleanField()
    certfile = fields.CharField(max_length=255)
    keyfile = fields.CharField(max_length=255)
