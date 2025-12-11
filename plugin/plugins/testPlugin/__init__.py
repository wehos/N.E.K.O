import logging
from plugin.sdk.base import NekoPluginBase


class HelloPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)  # 传递 ctx 给基类
        # 启用文件日志（同时输出到文件和控制台）
        self.file_logger = self.enable_file_logging(log_level=logging.INFO)
        self.logger = self.file_logger  # 使用file_logger作为主要logger
        self.plugin_id = ctx.plugin_id  # 使用 plugin_id
        self.file_logger.info("HelloPlugin initialized with file logging enabled")

    def run(self, message: str | None = None, **kwargs):
        # 简单返回一个字典结构
        self.file_logger.info(f"Running HelloPlugin with message: {message}")
        return {
            "hello": message or "world",
            "extra": kwargs,
        }
