# plugin_config_loader.py
import importlib
import tomllib  # Python 3.11 起内置；如果是老版本可以用 tomli

def load_plugin_from_toml(toml_path: str):
    with open(toml_path, "rb") as f:
        conf = tomllib.load(f)
    entry = conf["plugin"]["entry"]
    module_path, class_name = entry.split(":")
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls()  # 返回插件实例
