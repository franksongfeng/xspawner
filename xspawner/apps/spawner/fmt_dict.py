import json
import datetime
from decimal import Decimal
from types import FunctionType, ModuleType, MethodType, LambdaType
from enum import Enum
from collections.abc import Iterable
import inspect
import sys

def serialize_first_level_to_json(obj, indent=2, ensure_ascii=False):
    """
    以JSON字符串格式遍历返回字典第一级属性的序列化结果
    
    参数:
        obj: 要处理的字典对象
        indent: JSON缩进空格数（None表示紧凑格式）
        ensure_ascii: 是否确保ASCII输出（False可显示中文）
    
    返回:
        格式化的JSON字符串
    
    异常:
        TypeError: 当输入不是字典时抛出
    """
    if not isinstance(obj, dict):
        raise TypeError(f"输入必须是字典对象，当前类型: {type(obj).__name__}")
    
    # 用于存储遍历结果的字典
    result_dict = {}
    
   
    # 遍历第一级属性
    for key, value in obj.items():
        
        # 获取键的类型信息
        key_type = type(key).__name__
        
        # 判断值是否可以序列化
        if _is_value_serializable(value):
            value_type = type(value).__name__
                       
            try:
                # 尝试序列化值
                serialized_value = _serialize_value(value)
                result_dict[str(key)] = serialized_value
            except (TypeError, ValueError, OverflowError) as e:
                # 序列化失败，降级为类型信息
                value_type = type(value).__name__
                
                result_dict[str(key)] = value_type
        else:
            # 无法序列化，只提供类型信息
            value_type = type(value).__name__
                      
            result_dict[str(key)] = "<{} unserializable>".format(value_type)

    
    # 返回JSON字符串
    return json.dumps(result_dict, indent=indent, ensure_ascii=False, default=str)

def _is_value_serializable(value):
    """
    判断值是否可以JSON序列化
    
    参数:
        value: 要检查的值
    
    返回:
        bool: 是否可以序列化
    """
    # 基本可序列化类型
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    
    # 简单的容器类型（深度有限）
    if isinstance(value, (list, tuple)):
        if not value:  # 空容器总是可序列化的
            return True
        # 只检查前几个元素
        for item in list(value)[:3]:
            if not _is_simple_serializable(item):
                return False
        return True
    
    if isinstance(value, dict):
        if not value:  # 空字典总是可序列化的
            return True
        # 只检查前几个键值对
        for k, v in list(value.items())[:3]:
            if not _is_simple_serializable(k) or not _is_simple_serializable(v):
                return False
        return True
    
    # 特殊可序列化类型
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time, Decimal)):
        return True
    
    # 其他类型尝试序列化
    try:
        # 快速测试序列化
        json.dumps(value, default=str)
        return True
    except:
        return False

def _is_simple_serializable(value):
    """检查是否是简单的可序列化类型（不递归检查容器内部）"""
    return (
        value is None or 
        isinstance(value, (bool, int, float, str, datetime.datetime, 
                          datetime.date, datetime.time, Decimal))
    )

def _serialize_value(value):
    """
    序列化值
    
    参数:
        value: 要序列化的值
    
    返回:
        序列化后的值
    """
    # 基本类型直接返回
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    
    # 日期时间类型转换为字符串
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    
    if isinstance(value, datetime.date):
        return value.isoformat()
    
    if isinstance(value, datetime.time):
        return value.isoformat()
    
    if isinstance(value, Decimal):
        return float(value)  # 或 str(value)
    
    # 容器类型
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    
    if isinstance(value, dict):
        return {str(k): _serialize_value(v) for k, v in value.items()}
    
    # 其他类型使用默认的str转换
    return str(value)


def _get_value_brief(value):
    """
    获取值的简要描述
    
    参数:
        value: 对象值
    
    返回:
        简要描述字符串
    """
    if value is None:
        return "null"
    
    if isinstance(value, bool):
        return "true" if value else "false"
    
    if isinstance(value, (int, float)):
        return str(value)
    
    if isinstance(value, str):
        if len(value) > 30:
            return f'"{value[:27]}..."'
        return f'"{value}"'
    
    if isinstance(value, (list, tuple, set)):
        length = len(value)
        if length == 0:
            return "empty collection"
        return f"collection with {length} items"
    
    if isinstance(value, dict):
        length = len(value)
        if length == 0:
            return "empty dictionary"
        return f"dictionary with {length} keys"
    
    if isinstance(value, bytes):
        length = len(value)
        return f"bytes ({length} bytes)"
    
    # 其他情况返回类型名称
    return type(value).__name__

def _get_function_signature(func):
    """
    获取函数的签名信息
    
    参数:
        func: 函数对象
    
    返回:
        签名字符串
    """
    try:
        sig = inspect.signature(func)
        return str(sig)
    except:
        try:
            # 对于内置函数等无法获取签名的
            params = getattr(func, "__code__", None)
            if params:
                arg_count = params.co_argcount
                return f"({', '.join([f'arg{i}' for i in range(arg_count)])})"
        except:
            pass
    return "()"

# =============================================
# 便捷函数和使用示例
# =============================================

def get_first_level_json(data, pretty=True):
    """
    便捷函数：获取第一级属性的JSON字符串
    
    参数:
        data: 字典数据
        pretty: 是否美化输出
    
    返回:
        JSON字符串
    """
    try:
        return serialize_first_level_to_json(
            data, 
            indent=2 if pretty else None, 
            ensure_ascii=False
        )
    except TypeError as e:
        error_result = {
            "error": str(e),
            "input_type": type(data).__name__
        }
        return json.dumps(error_result, indent=2 if pretty else None, ensure_ascii=False)

def demo_serialization():
    """演示序列化功能"""
    
    # 自定义类
    class CustomClass:
        def __init__(self, name):
            self.name = name
            self.data = [1, 2, 3]
        
        def method(self):
            return self.name
    
    # 枚举
    class Status(Enum):
        PENDING = "pending"
        ACTIVE = "active"
        INACTIVE = "inactive"
    
    # 创建测试字典
    test_dict = {
        # 可序列化的值
        "name": "张三",
        "age": 30,
        "score": 95.5,
        "active": True,
        "none_value": None,
        "hobbies": ["reading", "coding", "music"],
        "metadata": {
            "created": "2024-01-01",
            "updated": "2024-05-18"
        },
        "scores": (85, 92, 78),
        "birth_date": datetime.date(1990, 5, 15),
        
        # 无法序列化的值
        "print_function": print,
        "json_module": json,
        "custom_class": CustomClass,
        "instance": CustomClass("test_instance"),
        "enum_value": Status.ACTIVE,
        "lambda_func": lambda x: x * 2,
        "method": CustomClass("test").method,
        "generator": (i for i in range(5)),
        "bytes_data": b"\x48\x65\x6c\x6c\x6f",  # "Hello"
        "set_data": {1, 2, 3, 2, 1},
        "complex_number": 3 + 4j,
        
        # 特殊键名
        123: "numeric_key",
        ("tuple", "key"): "tuple_as_key",
        
        # 大列表
        "large_list": list(range(100)),
    }
    
    print("原始字典包含以下键:")
    for i, key in enumerate(test_dict.keys(), 1):
        print(f"  {i:2d}. {key!r:20} : {type(test_dict[key]).__name__}")
    
    print("\n" + "=" * 80)
    print("序列化结果:")
    print("=" * 80)
    
    # 获取序列化结果
    json_result = get_first_level_json(test_dict, pretty=True)
    
    # 显示结果（控制长度）
    max_display_length = 1500
    if len(json_result) > max_display_length:
        print(json_result[:max_display_length])
        print(f"...\n(输出过长，已截断。完整长度: {len(json_result)} 字符)")
    else:
        print(json_result)
    
    # 解析并显示摘要
    print("\n" + "=" * 80)
    print("结果摘要:")
    print("=" * 80)
    
    parsed = json.loads(json_result)
    
    
    print(f"\n序列化后的键数: {len(parsed)}")
    
    # 显示几个示例条目
    print("\n示例条目:")
    sample_keys = list(parsed.keys())[:3]
    for key in sample_keys:
        entry = parsed[key]
        if entry["serializable"]:
            print(f"  {key}: {entry['value']} ({entry['type']})")
        else:
            print(f"  {key}: <{entry['type']}> - {entry['type_info']['brief']}")

if __name__ == "__main__":
    print("=" * 80)
    print("字典第一级属性JSON序列化演示")
    print("=" * 80)
    
    demo_serialization()
    
    print("\n" + "=" * 80)
    print("简单使用示例:")
    print("=" * 80)
    
    # 简单示例
    simple_dict = {
        "username": "john_doe",
        "age": 28,
        "email": "john@example.com",
        "registration_date": datetime.datetime.now(),
        "preferences": {"theme": "dark", "notifications": True},
        "logout_function": lambda: print("Logging out..."),
        "admin_module": sys
    }
    
    print("输入字典:")
    for k, v in simple_dict.items():
        print(f"  {k!r:20}: {type(v).__name__:15} = {_get_value_brief(v)}")
    
    print("\n序列化结果:")
    result = get_first_level_json(simple_dict, pretty=True)
    
    # 只显示结果的前500个字符
    print(result[:500])
    if len(result) > 500:
        print("...")