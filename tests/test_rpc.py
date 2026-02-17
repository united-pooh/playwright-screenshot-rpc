"""
JSON-RPC 处理层单元测试（无需真实浏览器）。
"""

import json
import pytest

from server.models import ErrorCode
from server.rpc_handler import RpcHandler
from server.screenshot_service import ScreenshotService


class FakeScreenshotService(ScreenshotService):
    """极简存根 – 重写 screenshot() 以避免启动 Playwright。"""

    async def start(self) -> None:  # noqa: D102
        self._browser = object()  # 真实性的哨兵对象

    async def stop(self) -> None:  # noqa: D102
        self._browser = None

    async def screenshot(self, params):  # noqa: D102
        from server.models import ScreenshotResult

        return ScreenshotResult(
            image="AAAA",
            image_type="png",
            width=100,
            height=100,
            size_bytes=3,
        )


@pytest.fixture
def service():
    svc = FakeScreenshotService()
    svc._browser = object()  # 标记为“已启动”
    return svc


@pytest.fixture
def handler(service):
    return RpcHandler(service)


# ── ping ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ping(handler):
    resp = await handler.handle({"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert resp["result"] == {"pong": True, "status": "在线"}
    assert resp["id"] == 1


# ── get_methods ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_methods(handler):
    resp = await handler.handle({"jsonrpc": "2.0", "method": "get_methods", "id": 2})
    assert "screenshot" in resp["result"]["methods"]
    assert "ping" in resp["result"]["methods"]


# ── screenshot ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_screenshot_basic(handler):
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "screenshot",
            "params": {"html": "<h1>Hello</h1>"},
            "id": 3,
        }
    )
    assert "error" not in resp or resp["error"] is None
    assert resp["result"]["image_type"] == "png"


@pytest.mark.asyncio
async def test_screenshot_missing_html(handler):
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "screenshot",
            "params": {},
            "id": 4,
        }
    )
    assert resp["error"]["code"] == ErrorCode.INVALID_PARAMS


# ── 协议错误 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_method_not_found(handler):
    resp = await handler.handle({"jsonrpc": "2.0", "method": "nope", "id": 5})
    assert resp["error"]["code"] == ErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_parse_error(handler):
    resp = await handler.handle(b"{bad json{{")
    assert resp["error"]["code"] == ErrorCode.PARSE_ERROR


@pytest.mark.asyncio
async def test_from_raw_bytes(handler):
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "ping",
            "id": "abc",
        }
    ).encode()
    resp = await handler.handle(payload)
    assert resp["result"] == {"pong": True, "status": "在线"}
    assert resp["id"] == "abc"


@pytest.mark.asyncio
async def test_notification(handler):
    """验证通知请求（没有 ID）不返回响应。"""
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "ping",
            # 没有 id 字段
        }
    )
    assert resp is None
