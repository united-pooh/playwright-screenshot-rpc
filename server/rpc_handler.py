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
    JobResponse,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    ScreenshotParams,
)
from server.task_manager import TaskManager

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
    无状态分发器。请传入一个 :class:`TaskManager` 实例；
    使用原始 JSON 字节或字典调用 :meth:`handle`。
    """

    def __init__(self, task_manager: TaskManager) -> None:
        self._task_manager = task_manager

    # ── 公共入口点 ────────────────────────────────────────────────────

    async def handle(self, raw: bytes | str | dict) -> dict:
        """
        处理 JSON-RPC 请求。

        *raw* 可以是：
          - ``bytes``  – 原始 HTTP 体
          - ``str``    – JSON 字符串
          - ``dict``   – 已解析的对象

        始终返回一个符合 JSON-RPC 2.0 规范的 ``dict``。
        """
        rpc_id = None
        try:
            payload = self._decode(raw)

            # 初步验证请求结构
            try:
                request = JsonRpcRequest.model_validate(payload)
            except ValidationError as exc:
                # 如果连结构都不对，尝试提取 id（如果存在）
                rpc_id = payload.get("id") if isinstance(payload, dict) else None
                raise _RpcError(
                    ErrorCode.INVALID_REQUEST,
                    "请求结构不符合 JSON-RPC 2.0 规范",
                    data=exc.errors(),
                ) from exc

            rpc_id = request.id
            result = await self._dispatch(request)

            # 规范：如果 id 为 None，则视为“通知请求”，不返回响应
            if rpc_id is None:
                return None

            return JsonRpcResponse(
                id=rpc_id,
                result=result,
            ).model_dump()

        except _RpcError as exc:
            return self._error_response(rpc_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("RpcHandler 捕获到未处理的异常 (ID: %r)", rpc_id)
            return self._error_response(
                rpc_id,
                ErrorCode.INTERNAL_ERROR,
                "服务器内部错误",
                data=None,  # 脱敏，不直接向客户端泄露异常详情
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
                ErrorCode.PARSE_ERROR,
                f"JSON 解析失败: {exc.msg}",
                data={"pos": exc.pos},
            ) from exc
        if not isinstance(obj, dict):
            raise _RpcError(ErrorCode.INVALID_REQUEST, "请求内容必须是一个 JSON 对象")
        return obj

    # ── 分发 ──────────────────────────────────────────────────────────────

    async def _dispatch(self, request: JsonRpcRequest) -> Any:
        """将请求路由到相应的方法处理程序。"""
        handler = _METHOD_REGISTRY.get(request.method)
        if handler is None:
            raise _RpcError(
                ErrorCode.METHOD_NOT_FOUND,
                f"找不到请求的方法: {request.method!r}",
            )

        logger.debug("正在执行 RPC 方法: %r (ID: %r)", request.method, request.id)
        return await handler(self._task_manager, request.params or {})

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
async def _handle_screenshot(task_manager: TaskManager, params: dict) -> dict:
    """
    执行截图任务并同步返回结果。
    """
    try:
        screenshot_params = ScreenshotParams.model_validate(params)
    except ValidationError as exc:
        errors = exc.errors()
        readable_errors = [
            f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}" for e in errors
        ]
        raise _RpcError(
            ErrorCode.INVALID_PARAMS,
            "截图参数校验失败",
            data={"details": readable_errors},
        ) from exc

    # 1. 提交任务到队列
    job_id = await task_manager.submit_task(screenshot_params)

    # 2. 等待结果 (同步阻塞，直接返回结果)
    job = await task_manager.wait_for_result(
        job_id, timeout=int(screenshot_params.timeout_ms / 1000) + 5
    )

    if not job:
        raise _RpcError(ErrorCode.TIMEOUT, f"任务执行超时 (ID: {job_id})")

    if job.status == "failed":
        raise _RpcError(
            ErrorCode.SCREENSHOT_FAILED,
            job.result.error if job.result else "任务执行失败",
            data={"job_id": job_id},
        )

    # 直接返回结果部分，不带外层 JobResponse 结构
    if job.result:
        return job.result.model_dump()

    raise _RpcError(ErrorCode.INTERNAL_ERROR, "任务结果异常")


@rpc_method("get_job_status")
async def _handle_get_job_status(task_manager: TaskManager, params: dict) -> dict:
    """查询异步任务的状态和结果。"""
    job_id = params.get("job_id")
    if not job_id:
        raise _RpcError(ErrorCode.INVALID_PARAMS, "缺少必填参数: job_id")

    job = await task_manager.get_job(job_id)
    if not job:
        raise _RpcError(ErrorCode.JOB_NOT_FOUND, f"找不到任务: {job_id}")

    return job.model_dump()


@rpc_method("ping")
async def _handle_ping(
    task_manager: TaskManager,
    params: dict,  # noqa: ARG001
) -> dict:
    """健康检查。"""
    return {"pong": True, "status": "在线"}


@rpc_method("get_methods")
async def _handle_get_methods(
    task_manager: TaskManager,
    params: dict,  # noqa: ARG001
) -> dict:
    """返回可用方法列表。"""
    return {"methods": sorted(_METHOD_REGISTRY.keys())}


# ── 内部异常 ────────────────────────────────────────────────────────


class _RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
