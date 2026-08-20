import inspect
from tortoise import Tortoise
from tortoise import fields, models
from urllib.parse import quote_plus


def get_caller_module():
    caller_frame = inspect.stack()[1]  # 索引0是当前函数，索引1是调用者
    caller_module = inspect.getmodule(caller_frame[0])
    return caller_module


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
    if category in ("mysql", "postgres"):
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
    '''
    初始化连接并建表
    (去 orm 模块中查找模型类)
    '''
    await Tortoise.init(
        db_url=conn_str,
        modules={
            'models': [get_caller_module().__name__]
        }
    )

    await Tortoise.generate_schemas(safe=True)


async def close_database():
    '''
    关闭所有数据库连接
    '''
    await Tortoise.close_connections()


class Concept(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.IntField(pk=True, generated=True)

        if '__str__' not in attrs:
            def auto_str(self):
                return f"{name}({self.id})"
            attrs['__str__'] = auto_str
        return super().__new__(cls, name, bases, attrs)


class Relation(models.ModelMeta):
    def __new__(cls, name, bases, attrs):
        if "id" not in attrs:
            attrs['id'] = fields.IntField(pk=True, generated=True)

        meta_class = attrs.get("Meta")
        if meta_class:
            fk_mapping = getattr(meta_class, "fk_mapping", {})
            if fk_mapping:
                for field_name, related_model in fk_mapping.items():
                    if field_name not in attrs:
                        attrs[field_name] = fields.ForeignKeyField(related_model, null=False)
                indexes = getattr(meta_class, "indexes", [])
                unique_together = getattr(meta_class, "unique_together", ())
                setattr(meta_class, "indexes", indexes + [tuple(fk_mapping.keys())])
                setattr(meta_class, "unique_together", unique_together + (tuple(fk_mapping.keys())))
        return super().__new__(cls, name, bases, attrs)


# class Province(models.Model, metaclass = Concept):
#     class Meta:
#         table = "Province"


# class City(models.Model, metaclass = Concept):
#     class Meta:
#         table = "City"


# class Year(models.Model, metaclass = Concept):
#     class Meta:
#         table = "Year"


# class ProvinceCity(models.Model, metaclass = Relation):
#     class Meta:
#         table = "ProvinceCity"
#         fk_mapping = {
#             "province": Province,
#             "city": City
#         }


# class CityStatistics(models.Model, metaclass = Relation):
#     class Meta:
#         table = "CityStatistics"
#         fk_mapping = {
#             "city": City,
#             "year": Year
#         }
#     gdp = fields.FloatField()
#     population = fields.FloatField()
