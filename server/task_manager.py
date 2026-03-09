"""
任务管理器 – 处理 Redis 队列的入队、结果存取及状态管理。
"""

import json
import time
import uuid
from typing import Any, Awaitable, Optional, cast

import redis.asyncio as redis

from config import settings
from server.models import JobResult, JobStatus, ScreenshotParams, ScreenshotResult


class TaskManager:
    # 临时结果队列的前缀 (用于同步等待)
    RESULT_QUEUE_PREFIX = "result_queue:"

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None

    def _ensure_connected(self) -> None:
        """确保 Redis 已连接。"""
        if self._redis is None:
            raise ConnectionError(
                "TaskManager is not connected to Redis. Call connect() first."
            )

    def _client(self) -> redis.Redis:
        """返回已连接的 Redis 客户端。"""
        self._ensure_connected()
        return cast(redis.Redis, self._redis)

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
            self._redis = None

    async def submit_task(self, params: ScreenshotParams) -> str:
        """
        创建一个新任务并推入队列（原子操作）。
        返回 job_id。
        """
        client = self._client()
        job_id = str(uuid.uuid4())
        now = time.time()

        job_result = JobResult(
            job_id=job_id, status="pending", created_at=now, updated_at=now
        )

        # 构建任务载荷
        task_payload = {"job_id": job_id, "params": params.model_dump()}

        # 使用 pipeline 确保初始状态存储和任务入队在同一个事务中执行
        async with client.pipeline(transaction=True) as pipe:
            # 1. 存储初始状态
            key = f"{settings.REDIS_RESULT_PREFIX}{job_id}"
            pipe.set(
                key, job_result.model_dump_json(), ex=settings.REDIS_RESULT_TTL_SECONDS
            )

            # 2. 推入任务队列 (存储 params 的 json)
            pipe.rpush(settings.REDIS_TASK_QUEUE, json.dumps(task_payload))

            await pipe.execute()

        return job_id

    async def get_job(self, job_id: str) -> Optional[JobResult]:
        """从 Redis 获取任务状态和结果。"""
        client = self._client()
        key = f"{settings.REDIS_RESULT_PREFIX}{job_id}"
        data = await client.get(key)
        if not data:
            return None
        return JobResult.model_validate_json(data)

    async def update_job_status(
        self, job_id: str, status: JobStatus, result: Optional[ScreenshotResult] = None
    ) -> None:
        """
        更新任务状态及结果，并通知等待者。
        按照要求：结果图片不存回 Redis 待取，仅通过结果队列返回。
        """
        client = self._client()
        job = await self.get_job(job_id)
        if not job:
            return

        job.status = status
        job.updated_at = time.time()
        job.result = result

        # 1. 如果任务完成，通过临时结果队列发送包含完整数据（含图片）的结果
        if status in ("success", "failed"):
            result_key = f"{self.RESULT_QUEUE_PREFIX}{job_id}"
            await cast(Awaitable[int], client.rpush(result_key, job.model_dump_json()))
            await cast(Awaitable[bool], client.expire(result_key, 60))

        # 2. 存回持久化状态时，移除图片数据以节省 Redis 空间 (用后即焚)
        if job.result and job.result.image:
            job.result.image = None  # 清除 Base64 图片数据

        await self._set_result(job_id, job)

    async def wait_for_result(
        self, job_id: str, timeout: int = 30
    ) -> Optional[JobResult]:
        """
        等待任务完成并返回结果。
        """
        client = self._client()
        result_key = f"{self.RESULT_QUEUE_PREFIX}{job_id}"
        # 使用 BLPOP 阻塞等待结果
        result = await cast(
            Awaitable[Optional[list[str]]],
            client.blpop([result_key], timeout=timeout),
        )
        if result:
            _, data = result
            return JobResult.model_validate_json(data)
        return None

    async def _set_result(self, job_id: str, job: JobResult) -> None:
        client = self._client()
        key = f"{settings.REDIS_RESULT_PREFIX}{job_id}"
        await client.set(
            key, job.model_dump_json(), ex=settings.REDIS_RESULT_TTL_SECONDS
        )

    async def pop_task(self, timeout: int = 5) -> Optional[dict[str, Any]]:
        """
        (Worker 使用) 从队列中阻塞式获取一个任务。
        """
        client = self._client()
        result = await cast(
            Awaitable[Optional[list[str]]],
            client.blpop([settings.REDIS_TASK_QUEUE], timeout=timeout),
        )
        if result:
            _, payload = result
            return cast(dict[str, Any], json.loads(payload))
        return None
