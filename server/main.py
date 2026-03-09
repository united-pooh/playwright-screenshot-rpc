"""
入口点：启动公开 JSON-RPC 端点的 aiohttp HTTP 服务器。

端点：
  POST /rpc  – 基于 HTTP 的 JSON-RPC 2.0
  GET  /     – 健康检查（返回 200 OK）
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from typing import Optional

from aiohttp import web

from config import settings
from server.rpc_handler import RpcHandler
from server.task_manager import TaskManager

# ── 日志设置 ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)
TASK_MANAGER_APP_KEY = web.AppKey("task_manager", TaskManager)
RPC_HANDLER_APP_KEY = web.AppKey("rpc_handler", RpcHandler)


class WorkerSubprocessManager:
    """托管 `server.worker` 子进程的轻量管理器。"""

    def __init__(
        self,
        python_executable: Optional[str] = None,
        restart_delay_seconds: float = 3.0,
    ) -> None:
        self._python_executable = python_executable or sys.executable
        self._restart_delay_seconds = restart_delay_seconds
        self._process: Optional[asyncio.subprocess.Process] = None
        self._watch_task: Optional[asyncio.Task[None]] = None
        self._stopping = False

    async def start(self) -> None:
        if self._watch_task and not self._watch_task.done():
            return

        self._stopping = False
        await self._spawn_worker()
        self._watch_task = asyncio.create_task(self._watch_worker())

    async def stop(self) -> None:
        self._stopping = True

        watch_task = self._watch_task
        self._watch_task = None
        if watch_task:
            watch_task.cancel()

        process = self._process
        self._process = None
        if process and process.returncode is None:
            logger.info("正在停止托管 Worker 子进程 (pid=%s)", process.pid)
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning(
                    "托管 Worker 子进程未在超时内退出，发送 SIGKILL (pid=%s)",
                    process.pid,
                )
                process.kill()
                await process.wait()

        if watch_task:
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task

    async def _spawn_worker(self) -> None:
        process = await asyncio.create_subprocess_exec(
            self._python_executable,
            "-m",
            "server.worker",
        )
        self._process = process
        logger.info("已拉起托管 Worker 子进程 (pid=%s)", process.pid)

    async def _watch_worker(self) -> None:
        while not self._stopping:
            process = self._process
            if process is None:
                return

            returncode = await process.wait()
            self._process = None
            if self._stopping:
                return

            logger.warning(
                "托管 Worker 子进程异常退出 (pid=%s, returncode=%s)，%.0f 秒后重启",
                process.pid,
                returncode,
                self._restart_delay_seconds,
            )
            await asyncio.sleep(self._restart_delay_seconds)
            if self._stopping:
                return

            try:
                await self._spawn_worker()
            except Exception:
                logger.exception("重启托管 Worker 子进程失败")
                await asyncio.sleep(self._restart_delay_seconds)


WORKER_MANAGER_APP_KEY = web.AppKey("worker_manager", WorkerSubprocessManager)


# ── aiohttp 请求处理程序 ──────────────────────────────────────────────────


async def handle_rpc(request: web.Request) -> web.Response:
    """
    处理 ``/rpc``。
    """
    if request.method == "OPTIONS":
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            },
        )

    if request.method != "POST":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32600,
                    "message": f"仅支持 POST 请求，你发送的是 {request.method}",
                },
                "id": None,
            },
            status=405,
        )

    handler: RpcHandler = request.app[RPC_HANDLER_APP_KEY]
    # ... 原有逻辑 ...

    try:
        # 尝试直接解析 JSON
        payload = await request.json()
    except json.JSONDecodeError as exc:
        logger.warning("JSON 解析失败: %s", exc)
        # 返回标准的 JSON-RPC 解析错误
        from server.models import ErrorCode

        error_resp = RpcHandler._error_response(
            None, ErrorCode.PARSE_ERROR, f"JSON 解析失败: {exc.msg}"
        )
        return web.json_response(error_resp)
    except Exception as exc:
        logger.warning("读取请求体失败: %s", exc)
        from server.models import ErrorCode

        error_resp = RpcHandler._error_response(
            None, ErrorCode.INVALID_REQUEST, "读取请求失败"
        )
        return web.json_response(error_resp)

    response_dict = await handler.handle(payload)

    # 如果是通知请求（handler 返回 None），则根据规范返回 204
    if response_dict is None:
        return web.Response(status=204)

    # 根据 JSON-RPC 2.0 规范，即使是错误，在 HTTP 层通常也返回 200 OK
    return web.json_response(response_dict)


async def handle_health(_request: web.Request) -> web.Response:
    """``GET /`` – 简单的存活探测。"""
    return web.Response(
        status=200,
        content_type="application/json",
        body=json.dumps({"status": "ok"}).encode(),
    )


# ── 应用工厂 ───────────────────────────────────────────────────────────────


def build_app(task_manager: TaskManager) -> web.Application:
    """构建并配置 aiohttp 应用程序。"""
    app = web.Application()
    app[TASK_MANAGER_APP_KEY] = task_manager
    app[RPC_HANDLER_APP_KEY] = RpcHandler(task_manager)

    app.router.add_get("/", handle_health)
    app.router.add_route("*", "/rpc", handle_rpc)

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)

    return app


async def _on_startup(app: web.Application) -> None:
    task_manager: TaskManager = app[TASK_MANAGER_APP_KEY]
    await task_manager.connect()
    if settings.AUTO_START_WORKER:
        worker_manager = WorkerSubprocessManager()
        app[WORKER_MANAGER_APP_KEY] = worker_manager
        await worker_manager.start()
        logger.info("API 服务已启动，Redis 已连接，托管 Worker 已拉起")
        return

    logger.info("API 服务已启动，Redis 已连接")


async def _on_cleanup(app: web.Application) -> None:
    worker_manager: Optional[WorkerSubprocessManager] = app.get(WORKER_MANAGER_APP_KEY)
    if worker_manager:
        await worker_manager.stop()

    task_manager: TaskManager = app[TASK_MANAGER_APP_KEY]
    await task_manager.disconnect()
    logger.info("API 服务已停止，Redis 连接已断开")


# ── 主程序 ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """启动 aiohttp 应用。"""
    task_manager = TaskManager()
    app = build_app(task_manager)

    logger.info(
        "正在启动分布式 API 服务器: %s:%d (Redis=%s:%d)",
        settings.HOST,
        settings.PORT,
        settings.REDIS_HOST,
        settings.REDIS_PORT,
    )

    # run_app 会自动处理信号 (SIGINT/SIGTERM) 并执行 cleanup 钩子
    web.run_app(
        app,
        host=settings.HOST,
        port=settings.PORT,
        print=None,  # 禁用默认的启动打印
        access_log=None,  # 禁用每条请求的访问日志
    )


if __name__ == "__main__":
    main()
