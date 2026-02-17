"""
核心截图服务 – 管理共享的 Playwright 浏览器实例，
并执行 HTML 渲染及元素/页面截图捕获。
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import struct
from typing import Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from config import settings
from server.models import (
    ClipRegion,
    ErrorCode,
    ScreenshotParams,
    ScreenshotResult,
)

logger = logging.getLogger(__name__)


class ScreenshotServiceError(Exception):
    """当截图操作失败时抛出。"""
    def __init__(self, message: str, code: int = ErrorCode.SCREENSHOT_FAILED):
        super().__init__(message)
        self.code = code


class ScreenshotService:
    """
    单例风格的服务，拥有一个 Playwright 浏览器。

    用法::

        service = ScreenshotService()
        await service.start()
        ...
        result = await service.screenshot(params)
        ...
        await service.stop()

    或者使用异步上下文管理器::

        async with ScreenshotService() as service:
            result = await service.screenshot(params)
    """
    
    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()
    
    # ── 生命周期 ─────────────────────────────────────────────────────────────
    
    async def start(self) -> None:
        """启动 Playwright 和浏览器。"""
        logger.info(
            "正在启动 Playwright (浏览器=%s, 无头模式=%s)",
            settings.BROWSER_TYPE,
            settings.HEADLESS,
        )
        self._playwright = await async_playwright().start()
        launcher = getattr(self._playwright, settings.BROWSER_TYPE)
        self._browser = await launcher.launch(headless=settings.HEADLESS)
        logger.info("浏览器已成功启动")
    
    async def stop(self) -> None:
        """关闭浏览器并停止 Playwright。"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("浏览器已停止")
    
    async def __aenter__(self) -> "ScreenshotService":
        await self.start()
        return self
    
    async def __aexit__(self, *_: object) -> None:
        await self.stop()
    
    # ── 公共 API ────────────────────────────────────────────────────────────
    
    async def screenshot(self, params: ScreenshotParams) -> ScreenshotResult:
        """
        在新的浏览器上下文/页面中渲染 *params.html*，
        并根据 *params* 进行截图。

        成功时返回 :class:`ScreenshotResult`。
        失败时抛出 :class:`ScreenshotServiceError`。
        """
        if self._browser is None:
            raise ScreenshotServiceError(
                "浏览器未启动", code=ErrorCode.BROWSER_ERROR
            )
        
        context: Optional[BrowserContext] = None
        try:
            context = await self._create_context(params)
            page = await context.new_page()
            image_bytes = await self._render_and_capture(page, params)
        except ScreenshotServiceError:
            raise
        except PlaywrightTimeoutError as exc:
            raise ScreenshotServiceError(str(exc), code=ErrorCode.TIMEOUT) from exc
        except Exception as exc:
            logger.exception("截图过程中出现非预期错误")
            raise ScreenshotServiceError(str(exc)) from exc
        finally:
            if context:
                await context.close()
        
        return self._build_result(image_bytes, params.image_type)
    
    # ── 内部辅助函数 ───────────────────────────────────────────────────────
    
    async def _create_context(self, params: ScreenshotParams) -> BrowserContext:
        """创建一个根据 *params* 配置的隔离浏览器上下文。"""
        context = await self._browser.new_context(
            viewport={
                "width": params.viewport.width,
                "height": params.viewport.height,
            },
            device_scale_factor=params.scale,
            extra_http_headers=params.extra_http_headers,
        )
        return context
    
    async def _render_and_capture(
            self, page: Page, params: ScreenshotParams
    ) -> bytes:
        """设置页面内容并捕获截图。"""
        
        # 在加载 HTML 之前注入样式覆盖
        html = self._inject_styles(params.html, params.style_overrides)
        
        logger.debug(
            "正在设置页面内容 (wait_until=%s, timeout=%d ms)",
            params.wait_until,
            params.timeout_ms,
        )
        await page.set_content(
            html,
            wait_until=params.wait_until,
            timeout=params.timeout_ms,
        )
        
        # 运行用户提供的任何脚本
        for script in params.scripts:
            await page.evaluate(script)
        
        # 如果有要求，等待额外的选择器
        if params.wait_for_selector:
            try:
                await page.wait_for_selector(
                    params.wait_for_selector,
                    timeout=settings.DEFAULT_WAIT_FOR_SELECTOR_TIMEOUT,
                )
            except PlaywrightTimeoutError as exc:
                raise ScreenshotServiceError(
                    f"未找到选择器 '{params.wait_for_selector}': {exc}",
                    code=ErrorCode.SELECTOR_NOT_FOUND,
                ) from exc
        
        # 构建截图参数
        shot_kwargs: dict = {
            "type": params.image_type,
            "full_page": params.full_page,
            "omit_background": params.omit_background,
        }
        if params.image_type == "jpeg":
            shot_kwargs["quality"] = params.quality
        
        # 显式裁剪区域具有最高优先级
        if params.clip:
            shot_kwargs["clip"] = self._clip_to_dict(params.clip)
            image_bytes: bytes = await page.screenshot(**shot_kwargs)
        
        # CSS 选择器 → 对匹配的元素进行截图
        elif params.selector:
            element = await page.query_selector(params.selector)
            if element is None:
                raise ScreenshotServiceError(
                    f"选择器 '{settings.selector}' 未匹配到任何元素",
                    code=ErrorCode.SELECTOR_NOT_FOUND,
                )
            # 移除 full_page；它不适用于元素截图
            shot_kwargs.pop("full_page", None)
            shot_kwargs.pop("omit_background", None)
            image_bytes = await element.screenshot(
                type=params.image_type,
                quality=params.quality if params.image_type == "jpeg" else None,
            )
        
        # 截取整个页面或视口
        else:
            image_bytes = await page.screenshot(**shot_kwargs)
        
        return image_bytes
    
    # ── 静态辅助函数 ────────────────────────────────────────────────────────
    
    @staticmethod
    def _inject_styles(html: str, css: Optional[str]) -> str:
        """将 *css* 注入到 HTML 的 <head> 中（或在前面添加 <style> 标签）。"""
        if not css:
            return html
        style_tag = f"<style>\n{css}\n</style>"
        lower = html.lower()
        head_close = lower.rfind("</head>")
        if head_close != -1:
            return html[:head_close] + style_tag + html[head_close:]
        return style_tag + html
    
    @staticmethod
    def _clip_to_dict(clip: ClipRegion) -> dict:
        return {
            "x": clip.x,
            "y": clip.y,
            "width": clip.width,
            "height": clip.height,
        }
    
    @staticmethod
    def _build_result(image_bytes: bytes, image_type: str) -> ScreenshotResult:
        """解析图像尺寸并封装结果。"""
        width, height = _parse_image_dimensions(image_bytes, image_type)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return ScreenshotResult(
            image=encoded,
            image_type=image_type,
            width=width,
            height=height,
            size_bytes=len(image_bytes),
        )


# ── 图像尺寸辅助函数 ───────────────────────────────────────────────────

def _parse_image_dimensions(data: bytes, image_type: str) -> tuple[int, int]:
    """返回 PNG 或 JPEG 字节数据的 (宽度, 高度)。"""
    try:
        if image_type == "png":
            return _png_dimensions(data)
        return _jpeg_dimensions(data)
    except Exception:
        return 0, 0


def _png_dimensions(data: bytes) -> tuple[int, int]:
    # PNG: 8 字节签名 + IHDR 块 (长度=4, 类型=4, 然后宽/高各 4 字节)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是有效的 PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    buf = io.BytesIO(data)
    buf.read(2)  # SOI 标记
    while True:
        marker, = struct.unpack(">H", buf.read(2))
        length, = struct.unpack(">H", buf.read(2))
        if marker in (0xFFC0, 0xFFC2):  # SOF0, SOF2
            buf.read(1)  # 精度
            height, width = struct.unpack(">HH", buf.read(4))
            return width, height
        buf.read(length - 2)
