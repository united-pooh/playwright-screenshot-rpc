"""
ScreenshotService 的集成测试。
需要安装 Playwright 浏览器：
    playwright install chromium
"""

import base64
import struct

import pytest
import pytest_asyncio

from server.models import ClipRegion, ScreenshotParams, Viewport
from server.screenshot_service import ScreenshotService, ScreenshotServiceError


@pytest_asyncio.fixture(scope="module")
async def service():
    """模块范围的服务，因此我们只启动一次浏览器。"""
    svc = ScreenshotService()
    await svc.start()
    yield svc
    await svc.stop()


def _is_valid_base64_png(data: str) -> bool:
    try:
        raw = base64.b64decode(data)
        return raw[:8] == b"\x89PNG\r\n\x1a\n"
    except Exception:
        return False


# ── 基础截图 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_page_png(service):
    params = ScreenshotParams(html="<h1>Hello, Playwright!</h1>")
    result = await service.screenshot(params)
    assert result.image_type == "png"
    assert result.size_bytes > 0
    assert _is_valid_base64_png(result.image)


@pytest.mark.asyncio
async def test_jpeg_output(service):
    params = ScreenshotParams(
        html="<div style='background:red;width:100px;height:100px'></div>",
        image_type="jpeg",
        quality=80,
    )
    result = await service.screenshot(params)
    assert result.image_type == "jpeg"
    assert result.size_bytes > 0


# ── 选择器截图 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_selector_screenshot(service):
    html = """
    <html><body>
      <div id="box" style="width:200px;height:200px;background:blue"></div>
    </body></html>
    """
    params = ScreenshotParams(html=html, selector="#box")
    result = await service.screenshot(params)
    assert result.width == 200
    assert result.height == 200


@pytest.mark.asyncio
async def test_selector_not_found_raises(service):
    params = ScreenshotParams(html="<p>hi</p>", selector="#does-not-exist")
    with pytest.raises(ScreenshotServiceError) as exc_info:
        await service.screenshot(params)
    from server.models import ErrorCode
    assert exc_info.value.code == ErrorCode.SELECTOR_NOT_FOUND


# ── 裁剪区域 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clip_region(service):
    html = "<html><body style='margin:0'><div style='width:800px;height:600px;background:green'></div></body></html>"
    params = ScreenshotParams(
        html=html,
        clip=ClipRegion(x=0, y=0, width=100, height=100),
    )
    result = await service.screenshot(params)
    assert result.width == 100
    assert result.height == 100


# ── 样式注入 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_style_injection(service):
    html = "<html><body><p id='t'>text</p></body></html>"
    params = ScreenshotParams(
        html=html,
        selector="#t",
        style_overrides="#t { color: red; font-size: 48px; }",
    )
    result = await service.screenshot(params)
    assert result.size_bytes > 0


# ── 视口 ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_viewport(service):
    params = ScreenshotParams(
        html="<html><body></body></html>",
        viewport=Viewport(width=800, height=600),
        full_page=False,
    )
    result = await service.screenshot(params)
    assert result.width == 800
    assert result.height == 600


# ── 脚本执行 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_script_execution(service):
    html = "<html><body><div id='d'>original</div></body></html>"
    params = ScreenshotParams(
        html=html,
        scripts=["document.getElementById('d').textContent = 'modified'"],
        selector="#d",
    )
    result = await service.screenshot(params)
    assert result.size_bytes > 0
