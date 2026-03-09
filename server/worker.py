"""
Worker 进程 – 异步消费 Redis 任务队列并执行截图。
"""

import asyncio
import logging
from pathlib import Path
import signal
import sys
import time
from typing import Optional

from config import settings
from server.models import ScreenshotParams, ScreenshotResult
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
    def __init__(
        self,
        service: Optional[ScreenshotService] = None,
        task_manager: Optional[TaskManager] = None,
    ) -> None:
        self.service = service or ScreenshotService()
        self.task_manager = task_manager or TaskManager()
        self.should_exit = False
        self._started_at = time.monotonic()
        self._completed_tasks = 0

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
                    self._request_restart_if_needed(include_task_limit=False, idle=True)
                    continue

                job_id = task_data["job_id"]
                params_dict = task_data["params"]
                task_completed = False

                logger.info("开始处理任务: %s", job_id)
                await self.task_manager.update_job_status(job_id, "processing")

                # 2. 执行截图
                try:
                    params = ScreenshotParams.model_validate(params_dict)
                    result = await self.service.screenshot(params)

                    # 3. 成功：存回结果
                    await self.task_manager.update_job_status(job_id, "success", result)
                    task_completed = True
                    logger.info("任务完成: %s", job_id)

                except ScreenshotServiceError as exc:
                    # 截图层已知错误
                    err_result = ScreenshotResult(error=str(exc))
                    await self.task_manager.update_job_status(
                        job_id, "failed", err_result
                    )
                    task_completed = True
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
                    task_completed = True

                if task_completed:
                    self._completed_tasks += 1
                    self._request_restart_if_needed(
                        include_task_limit=True,
                        idle=False,
                    )

            except Exception as exc:
                logger.error("Worker 循环出现异常: %s", exc)
                await asyncio.sleep(1)

    def _request_restart_if_needed(
        self,
        *,
        include_task_limit: bool,
        idle: bool,
    ) -> None:
        if self.should_exit:
            return

        reason = self._restart_reason(include_task_limit=include_task_limit)
        if reason is None:
            return

        trigger = "空闲检查" if idle else "任务完成后"
        logger.warning("Worker 触发自重启 (%s): %s", trigger, reason)
        self.should_exit = True

    def _restart_reason(self, *, include_task_limit: bool) -> Optional[str]:
        if (
            include_task_limit
            and settings.WORKER_MAX_TASKS > 0
            and self._completed_tasks >= settings.WORKER_MAX_TASKS
        ):
            return (
                "累计完成任务数达到阈值 "
                f"({self._completed_tasks}/{settings.WORKER_MAX_TASKS})"
            )

        age_seconds = int(time.monotonic() - self._started_at)
        if (
            settings.WORKER_MAX_AGE_SECONDS > 0
            and age_seconds >= settings.WORKER_MAX_AGE_SECONDS
        ):
            return (
                f"运行时长达到阈值 ({age_seconds}s/{settings.WORKER_MAX_AGE_SECONDS}s)"
            )

        if settings.WORKER_MAX_RSS_MB <= 0:
            return None

        rss_mb = self._read_rss_mb()
        if rss_mb is None:
            return None
        if rss_mb >= settings.WORKER_MAX_RSS_MB:
            return f"RSS 达到阈值 ({rss_mb:.1f}MiB/{settings.WORKER_MAX_RSS_MB}MiB)"
        return None

    def _read_rss_mb(self) -> Optional[float]:
        try:
            status_text = Path("/proc/self/status").read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("无法读取 /proc/self/status，跳过 RSS 检查: %s", exc)
            return None

        for line in status_text.splitlines():
            if not line.startswith("VmRSS:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                break
            try:
                rss_kb = int(parts[1])
            except ValueError:
                break
            return rss_kb / 1024

        logger.warning("无法从 /proc/self/status 解析 VmRSS，跳过 RSS 检查")
        return None


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
