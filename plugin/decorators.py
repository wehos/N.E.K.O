# neko_plugin_core/decorators.py
from typing import Type, Callable
from .plugin_base import PluginMeta, NEKO_PLUGIN_META_ATTR
from .entry_base import EntryMeta

def neko_plugin(
    id: str,
    name: str,
    version: str = "0.1.0",
    description: str = "",
):
    """给插件主 class 打标记 + 塞元数据."""
    def decorator(cls: Type):
        meta = PluginMeta(
            id=id,
            name=name,
            version=version,
            description=description,
        )
        setattr(cls, NEKO_PLUGIN_META_ATTR, meta)
        return cls
    return decorator


def plugin_entry(
    id: str,
    name: str,
    description: str = "",
    input_schema: dict | None = None,
    kind: str = "action",      # "action" 或 "service"
    auto_start: bool = False,
):
    """给插件内部的方法打入口标记."""
    def decorator(fn: Callable):
        meta = EntryMeta(
            id=id,
            name=name,
            description=description,
            input_schema=input_schema or {},
            kind=kind,              # "service" or "action"
            auto_start=auto_start,
        )
        setattr(fn, "__neko_entry_meta__", meta)
        return fn
    return decorator
