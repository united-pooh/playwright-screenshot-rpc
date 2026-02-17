"""
入口点：启动公开 JSON-RPC 端点的 aiohttp HTTP 服务器。

端点：
  POST /rpc  – 基于 HTTP 的 JSON-RPC 2.0
  GET  /     – 健康检查（返回 200 OK）
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys

from aiohttp import web

from config import settings
from rpc_handler import RpcHandler
from server.screenshot_service import ScreenshotService

# ── 日志设置 ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── aiohttp 请求处理程序 ──────────────────────────────────────────────────

async def handle_rpc(request: web.Request) -> web.Response:
    """
    处理 ``POST /rpc``。

    接受 JSON-RPC 2.0 请求体并返回 JSON-RPC 2.0 响应。
    """
    handler: RpcHandler = request.app["rpc_handler"]
    
    try:
        body = await request.read()
    except Exception as exc:
        logger.warning("读取请求体失败: %s", exc)
        return web.Response(status=400, text="读取请求体失败")
    
    response_dict = await handler.handle(body)
    
    # 通知没有响应 id – 仍然返回 204 或结果
    return web.Response(
        status=200,
        content_type="application/json",
        body=json.dumps(response_dict, ensure_ascii=False).encode("utf-8"),
    )


async def handle_health(_request: web.Request) -> web.Response:
    """``GET /`` – 简单的存活探测。"""
    return web.Response(
        status=200,
        content_type="application/json",
        body=json.dumps({"status": "ok"}).encode(),
    )


# ── 应用工厂 ───────────────────────────────────────────────────────────────

def build_app(service: ScreenshotService) -> web.Application:
    """构建并配置 aiohttp 应用程序。"""
    app = web.Application()
    app["screenshot_service"] = service
    app["rpc_handler"] = RpcHandler(service)
    
    app.router.add_get("/", handle_health)
    app.router.add_post("/rpc", handle_rpc)
    
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    
    return app


async def _on_startup(app: web.Application) -> None:
    service: ScreenshotService = app["screenshot_service"]
    await service.start()
    logger.info("截图服务已启动")


async def _on_cleanup(app: web.Application) -> None:
    service: ScreenshotService = app["screenshot_service"]
    await service.stop()
    logger.info("截图服务已停止")


# ── 主程序 ──────────────────────────────────────────────────────────────────────

def main() -> None:
    service = ScreenshotService()
    app = build_app(service)
    
    logger.info(
        "正在启动服务器: %s:%d (浏览器=%s)",
        settings.HOST,
        settings.PORT,
        settings.BROWSER_TYPE,
    )
    
    # 收到 SIGTERM / SIGINT 时优雅停机
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    runner = web.AppRunner(app)
    
    async def _run() -> None:
        await runner.setup()
        site = web.TCPSite(runner, settings.HOST, settings.PORT)
        await site.start()
        logger.info("服务器准备就绪 – 监听地址: http://%s:%d/rpc", settings.HOST, settings.PORT)
        
        stop_event = asyncio.Event()
        
        def _signal_handler() -> None:
            logger.info("收到停机信号")
            stop_event.set()
        
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows 不支持为所有信号添加信号处理程序
                pass
        
        await stop_event.wait()
        logger.info("正在关机...")
        await runner.cleanup()
    
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
        logger.info("服务器已停止")


if __name__ == "__main__":
    main()
