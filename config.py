"""
Playwright 截图服务器的全局配置类。
仅定义配置架构，不存储实际参数。
实际值由根目录下的 .env 文件或环境变量提供。
"""

from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    配置类架构。所有值必须从 .env 或环境变量提供。
    """

    # ── 服务器设置 ──────────────────────────────────────────────────────────────
    HOST: str
    PORT: int
    MAX_CONCURRENT_SCREENSHOTS: int

    # ── Playwright 设置 ──────────────────────────────────────────────────────────
    BROWSER_TYPE: Literal["chromium", "firefox", "webkit"]
    HEADLESS: bool
    VIEWPORT_WIDTH: int
    VIEWPORT_HEIGHT: int

    # ── 截图默认值 ──────────────────────────────────────────────────────────────
    DEFAULT_IMAGE_TYPE: Literal["png", "jpeg"]
    DEFAULT_IMAGE_QUALITY: int
    DEFAULT_WAIT_UNTIL: Literal["load", "domcontentloaded", "networkidle"]
    DEFAULT_TIMEOUT_MS: int
    DEFAULT_WAIT_FOR_SELECTOR_TIMEOUT: int

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    REDIS_PASSWORD: Optional[str] = None
    REDIS_TASK_QUEUE: str
    REDIS_RESULT_PREFIX: str
    REDIS_RESULT_TTL_SECONDS: int

    # ── JSON-RPC ─────────────────────────────────────────────────────────────────
    JSON_RPC_VERSION: str

    # ── 日志 ────────────────────────────────────────────────────────────────────
    LOG_LEVEL: str

    # Pydantic Settings 配置
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


# 导出全局单例
settings = Settings()
