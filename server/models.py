"""
用于 JSON-RPC 请求/响应验证的 Pydantic 模型。
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union
from enum import IntEnum
from dataclasses import dataclass
from pydantic import BaseModel, Field, field_validator
from config import settings


# ── JSON-RPC 基础结构 ─────────────────────────────────────────────────


class JsonRpcRequest(BaseModel):
    """标准的 JSON-RPC 2.0 请求外壳。"""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Optional[dict[str, Any]] = None
    id: Optional[Union[str, int]] = None


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JsonRpcResponse(BaseModel):
    """标准的 JSON-RPC 2.0 响应外壳。"""

    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[Any] = None
    error: Optional[JsonRpcError] = None


# ── 错误代码 (JSON-RPC 标准 + 自定义) ─────────────────────────────────


class ErrorCode(IntEnum):
    """JSON-RPC 2.0 错误代码枚举。"""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # 自定义截图服务错误
    SCREENSHOT_FAILED = -32001
    BROWSER_ERROR = -32002
    SELECTOR_NOT_FOUND = -32003
    TIMEOUT = -32004
    JOB_NOT_FOUND = -32005


# ── 状态与任务模型 ──────────────────────────────────────────────────


JobStatus = Literal["pending", "processing", "success", "failed"]


class JobResponse(BaseModel):
    """异步任务提交后的初始响应。"""

    job_id: str
    status: JobStatus = "pending"


class Viewport(BaseModel):
    width: int = Field(
        default=settings.VIEWPORT_WIDTH,
        ge=1,
        le=7680,
        description="视口宽度（像素）",
    )
    height: int = Field(
        default=settings.VIEWPORT_HEIGHT,
        ge=1,
        le=4320,
        description="视口高度（像素）",
    )


# ── 裁剪区域模型 ─────────────────────────────────────────────────────────


class ClipRegion(BaseModel):
    """用于裁剪截图的显式像素矩形。"""

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


# ── 截图参数 (RPC 调用中的 inner params 对象) ───────────────


class ScreenshotParams(BaseModel):
    """
    ``screenshot`` JSON-RPC 方法接受的参数。

    必填项：
      - html: 要渲染的原始 HTML 字符串。

    可选的目标选择/区域：
      - selector          : CSS 选择器 – 截取第一个匹配元素的截图。
      - clip              : 显式像素矩形（优先级高于 selector）。
      - full_page         : 捕获整个可滚动页面（默认为 False）。

    渲染选项：
      - viewport          : 浏览器视口大小。
      - wait_until        : 何时认为导航已完成。
      - wait_for_selector : 截图前等待的额外选择器。
      - timeout_ms        : 最大等待时间（毫秒）。
      - extra_http_headers: 额外的 HTTP 请求头（转发给页面）。
      - style_overrides   : 渲染前注入到 <head> 的内联 CSS。
      - scripts           : 截图前执行的 JS 脚本片段。

    输出选项：
      - image_type        : "png" 或 "jpeg"。
      - quality           : JPEG 质量 1-100（PNG 格式下忽略）。
      - scale             : 设备缩放因子 / 像素比（默认为 1）。
      - omit_background   : 隐藏默认背景（仅限 PNG，默认为 False）。
      - encoding          : "base64" 或 "binary"；服务器在 JSON 中始终返回 base64，
                            因此这仅控制响应中的标签。
    """

    html: str = Field(..., description="要渲染的 HTML 内容")

    # 目标定位
    selector: Optional[str] = Field(
        default=None,
        description="要截图的元素的 CSS 选择器",
    )
    clip: Optional[ClipRegion] = Field(
        default=None,
        description="显式像素矩形；优先级高于 selector",
    )
    full_page: bool = Field(
        default=False,
        description="捕获整个可滚动页面",
    )

    # 渲染设置
    viewport: Viewport = Field(default_factory=Viewport)
    wait_until: Literal["load", "domcontentloaded", "networkidle"] = Field(
        default=settings.DEFAULT_WAIT_UNTIL,
    )
    wait_for_selector: Optional[str] = Field(
        default=None,
        description="截图前要等待的额外选择器",
    )
    timeout_ms: int = Field(
        default=settings.DEFAULT_TIMEOUT_MS,
        ge=0,
        le=120_000,
    )
    extra_http_headers: dict[str, str] = Field(default_factory=dict)
    style_overrides: Optional[str] = Field(
        default=None,
        description="注入到 <head> 的原始 CSS",
    )

    # 输出设置
    image_type: Literal["png", "jpeg"] = Field(default=settings.DEFAULT_IMAGE_TYPE)
    quality: int = Field(
        default=settings.DEFAULT_IMAGE_QUALITY,
        ge=1,
        le=100,
    )
    scale: float = Field(default=1.0, ge=0.1, le=4.0)
    omit_background: bool = Field(default=False)
    encoding: Literal["base64", "binary"] = Field(default="base64")

    @field_validator("html")
    @classmethod
    def html_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("html 不能为空")
        return v


# ── 截图结果 ─────────────────────────────────────────────────────────


class ScreenshotResult(BaseModel):
    """成功时在 JsonRpcResponse.result 中返回。"""

    image: Optional[str] = Field(default=None, description="Base64 编码的图像字节")
    image_type: Optional[str] = Field(default=None, description="'png' 或 'jpeg'")
    width: Optional[int] = Field(default=None, description="捕获图像的实际像素宽度")
    height: Optional[int] = Field(default=None, description="捕获图像的实际像素高度")
    size_bytes: Optional[int] = Field(
        default=None, description="Base64 编码前的原始字节大小"
    )
    error: Optional[str] = Field(default=None, description="任务执行失败时的错误详情")


class JobResult(BaseModel):
    """查询任务结果时返回的完整模型。"""

    job_id: str
    status: JobStatus
    result: Optional[ScreenshotResult] = None
    created_at: float
    updated_at: float
