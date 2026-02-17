"""
JSON-RPC 2.0 请求分发器。

解析传入的 JSON 体，路由方法调用，并序列化响应。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import ValidationError

from server.models import (
    ErrorCode,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    ScreenshotParams,
)
from server.screenshot_service import ScreenshotService, ScreenshotServiceError

logger = logging.getLogger(__name__)

# 映射 方法名 → 处理程序协程
_METHOD_REGISTRY: dict[str, Any] = {}


def rpc_method(name: str):
    """用于将协程注册为 JSON-RPC 方法处理程序的装饰器。"""
    def decorator(fn):
        _METHOD_REGISTRY[name] = fn
        return fn
    return decorator


class RpcHandler:
    """
    无状态分发器。请传入一个 :class:`ScreenshotService` 实例；
    使用原始 JSON 字节或字典调用 :meth:`handle`。
    """
    
    def __init__(self, service: ScreenshotService) -> None:
        self._service = service
    
    # ── 公共入口点 ────────────────────────────────────────────────────
    
    async def handle(self, raw: bytes | str | dict) -> dict:
        """
        处理 JSON-RPC 请求。

        *raw* 可以是：
          - ``bytes``  – 原始 HTTP 体
          - ``str``    – JSON 字符串
          - ``dict``   – 已解析的对象（例如来自测试）

        始终返回一个可 JSON 序列化的 ``dict``。
        通知（id 为空/缺失）会被处理但不返回任何内容
        （服务器层不应为这些请求发送响应体）。
        """
        rpc_id = None
        try:
            payload = self._decode(raw)
            request = JsonRpcRequest.model_validate(payload)
            rpc_id = request.id
            result = await self._dispatch(request)
            return JsonRpcResponse(
                id=rpc_id,
                result=result,
            ).model_dump()
        
        except _RpcError as exc:
            return self._error_response(rpc_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("RpcHandler.handle 中出现未处理的异常")
            return self._error_response(
                rpc_id,
                ErrorCode.INTERNAL_ERROR,
                f"内部错误: {exc}",
            )
    
    # ── 解码与验证 ─────────────────────────────────────────────────────
    
    @staticmethod
    def _decode(raw: bytes | str | dict) -> dict:
        if isinstance(raw, dict):
            return raw
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _RpcError(
                ErrorCode.PARSE_ERROR, f"解析错误: {exc}"
            ) from exc
        if not isinstance(obj, dict):
            raise _RpcError(ErrorCode.INVALID_REQUEST, "请求必须是一个 JSON 对象")
        return obj
    
    # ── 分发 ──────────────────────────────────────────────────────────────
    
    async def _dispatch(self, request: JsonRpcRequest) -> Any:
        """将请求路由到相应的方法处理程序。"""
        handler = _METHOD_REGISTRY.get(request.method)
        if handler is None:
            raise _RpcError(
                ErrorCode.METHOD_NOT_FOUND,
                f"未找到方法: {request.method!r}",
            )
        logger.debug("正在分发方法=%r id=%r", request.method, request.id)
        return await handler(self._service, request.params or {})
    
    # ── 辅助函数 ───────────────────────────────────────────────────────────────
    
    @staticmethod
    def _error_response(
            rpc_id: Optional[Any],
            code: int,
            message: str,
            data: Optional[Any] = None,
    ) -> dict:
        return JsonRpcResponse(
            id=rpc_id,
            error=JsonRpcError(code=code, message=message, data=data),
        ).model_dump()


# ── 方法处理程序 ───────────────────────────────────────────────────────────

@rpc_method("screenshot")
async def _handle_screenshot(
        service: ScreenshotService, params: dict
) -> dict:
    """
    对渲染后的 HTML 进行截图。

    JSON-RPC 方法：``screenshot``
    参数：参见 :class:`~server.models.ScreenshotParams`
    返回：序列化的 :class:`~server.models.ScreenshotResult`
    """
    try:
        screenshot_params = ScreenshotParams.model_validate(params)
    except ValidationError as exc:
        raise _RpcError(
            ErrorCode.INVALID_PARAMS,
            "无效参数",
            data=exc.errors(),
        ) from exc
    
    try:
        result = await service.screenshot(screenshot_params)
    except ScreenshotServiceError as exc:
        raise _RpcError(exc.code, str(exc)) from exc
    
    return result.model_dump()


@rpc_method("ping")
async def _handle_ping(
        service: ScreenshotService, params: dict  # noqa: ARG001
) -> dict:
    """健康检查方法。返回 ``{"pong": true}``。"""
    return {"pong": True}


@rpc_method("get_methods")
async def _handle_get_methods(
        service: ScreenshotService, params: dict  # noqa: ARG001
) -> dict:
    """返回已注册的 JSON-RPC 方法列表。"""
    return {"methods": sorted(_METHOD_REGISTRY.keys())}


# ── 内部异常 ────────────────────────────────────────────────────────

class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
