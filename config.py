"""
Playwright 截图服务器的全局配置类。
使用 Pydantic Settings 从环境变量或 .env 加载配置。
"""

from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    配置类，自动从环境变量或 .env 文件加载数据。
    """
    
    # ── 服务器设置 ──────────────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8080
    
    # ── Playwright 设置 ──────────────────────────────────────────────────────────
    BROWSER_TYPE: Literal["chromium", "firefox", "webkit"] = "chromium"
    HEADLESS: bool = True
    VIEWPORT_WIDTH: int = 1280
    VIEWPORT_HEIGHT: int = 720
    
    # ── 截图默认值 ──────────────────────────────────────────────────────────────
    DEFAULT_IMAGE_TYPE: Literal["png", "jpeg"] = "png"
    DEFAULT_IMAGE_QUALITY: int = 90
    DEFAULT_WAIT_UNTIL: Literal["load", "domcontentloaded", "networkidle"] = "networkidle"
    DEFAULT_TIMEOUT_MS: int = 30000
    DEFAULT_WAIT_FOR_SELECTOR_TIMEOUT: int = 10000
    
    # ── JSON-RPC ─────────────────────────────────────────────────────────────────
    JSON_RPC_VERSION: str = "2.0"
    
    # ── 日志 ────────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    
    # Pydantic Settings 配置
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# 导出全局单例
settings = Settings()
