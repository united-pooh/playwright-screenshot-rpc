"""
任务管理器 – 处理 Redis 队列的入队、结果存取及状态管理。
"""

import json
import time
import uuid
from typing import Optional

import redis.asyncio as redis

from config import settings
from server.models import JobResult, JobStatus, ScreenshotParams, ScreenshotResult


class TaskManager:
    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None

    async def connect(self) -> None:
        """连接到 Redis。"""
        self._redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )

    async def disconnect(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis:
            await self._redis.close()

    async def submit_task(self, params: ScreenshotParams) -> str:
        """
        创建一个新任务并推入队列。
        返回 job_id。
        """
        job_id = str(uuid.uuid4())
        now = time.time()

        job_result = JobResult(
            job_id=job_id, status="pending", created_at=now, updated_at=now
        )

        # 1. 存储初始状态
        await self._set_result(job_id, job_result)

        # 2. 推入任务队列 (存储 params 的 json)
        task_payload = {"job_id": job_id, "params": params.model_dump()}
        await self._redis.rpush(settings.REDIS_TASK_QUEUE, json.dumps(task_payload))

        return job_id

    async def get_job(self, job_id: str) -> Optional[JobResult]:
        """从 Redis 获取任务状态和结果。"""
        key = f"{settings.REDIS_RESULT_PREFIX}{job_id}"
        data = await self._redis.get(key)
        if not data:
            return None
        return JobResult.model_validate_json(data)

    async def update_job_status(
        self, job_id: str, status: JobStatus, result: Optional[ScreenshotResult] = None
    ) -> None:
        """更新任务状态及结果。"""
        job = await self.get_job(job_id)
        if not job:
            return

        job.status = status
        job.updated_at = time.time()
        if result:
            job.result = result

        await self._set_result(job_id, job)

    async def _set_result(self, job_id: str, job: JobResult) -> None:
        key = f"{settings.REDIS_RESULT_PREFIX}{job_id}"
        await self._redis.set(
            key, job.model_dump_json(), ex=settings.REDIS_RESULT_TTL_SECONDS
        )

    async def pop_task(self, timeout: int = 5) -> Optional[dict]:
        """
        (Worker 使用) 从队列中阻塞式获取一个任务。
        """
        result = await self._redis.blpop(settings.REDIS_TASK_QUEUE, timeout=timeout)
        if result:
            _, payload = result
            return json.loads(payload)
        return None
