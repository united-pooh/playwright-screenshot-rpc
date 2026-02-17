"""
Worker 进程 – 异步消费 Redis 任务队列并执行截图。
"""

import asyncio
import logging
import signal
import sys

from config import settings
from server.models import ErrorCode, ScreenshotParams, ScreenshotResult
from server.screenshot_service import ScreenshotService, ScreenshotServiceError
from server.task_manager import TaskManager

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("worker")


class Worker:
    def __init__(self) -> None:
        self.service = ScreenshotService()
        self.task_manager = TaskManager()
        self.should_exit = False

    async def start(self) -> None:
        """启动 Worker 资源。"""
        await self.service.start()
        await self.task_manager.connect()
        logger.info("Worker 已启动，正在等待任务...")

    async def stop(self) -> None:
        """释放 Worker 资源。"""
        await self.service.stop()
        await self.task_manager.disconnect()
        logger.info("Worker 已停止")

    async def run(self) -> None:
        """主循环。"""
        while not self.should_exit:
            try:
                # 1. 获取任务 (阻塞式)
                task_data = await self.task_manager.pop_task(timeout=5)
                if not task_data:
                    continue

                job_id = task_data["job_id"]
                params_dict = task_data["params"]

                logger.info("开始处理任务: %s", job_id)
                await self.task_manager.update_job_status(job_id, "processing")

                # 2. 执行截图
                try:
                    params = ScreenshotParams.model_validate(params_dict)
                    result = await self.service.screenshot(params)

                    # 3. 成功：存回结果
                    await self.task_manager.update_job_status(job_id, "success", result)
                    logger.info("任务完成: %s", job_id)

                except ScreenshotServiceError as exc:
                    # 截图层已知错误
                    err_result = ScreenshotResult(error=str(exc))
                    await self.task_manager.update_job_status(
                        job_id, "failed", err_result
                    )
                    logger.warning("任务失败 (ServiceError): %s - %s", job_id, exc)

                except Exception as exc:
                    # 未知错误
                    logger.exception("处理任务时出现非预期错误: %s", job_id)
                    err_result = ScreenshotResult(
                        error=f"内部错误: {type(exc).__name__}"
                    )
                    await self.task_manager.update_job_status(
                        job_id, "failed", err_result
                    )

            except Exception as exc:
                logger.error("Worker 循环出现异常: %s", exc)
                await asyncio.sleep(1)


async def main() -> None:
    worker = Worker()

    # 注册信号处理
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: setattr(worker, "should_exit", True))

    await worker.start()
    try:
        await worker.run()
    finally:
        await worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
