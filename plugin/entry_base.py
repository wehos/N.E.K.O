# neko_plugin_core/entry_base.py
from dataclasses import dataclass
from typing import Dict, Any, Callable, Literal

@dataclass
class EntryMeta:
    id: str
    name: str
    description: str = ""
    input_schema: Dict[str, Any] | None = None
    kind: Literal["service", "action"] = "action"  # service = 启动型入口
    auto_start: bool = False

@dataclass
class Entry:
    meta: EntryMeta
    handler: Callable  # 具体调用的函数/方法
