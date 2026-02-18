"""
JSON-RPC 处理层单元测试（无需真实浏览器）。
"""

import json
import pytest
import uuid
import time

from server.models import ErrorCode, JobResult, ScreenshotParams, ScreenshotResult
from server.rpc_handler import RpcHandler
from server.task_manager import TaskManager


class FakeTaskManager(TaskManager):
    """内存版本的任务管理器，不依赖 Redis。"""

    def __init__(self) -> None:
        self.jobs = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def submit_task(self, params: ScreenshotParams) -> str:
        job_id = str(uuid.uuid4())
        now = time.time()
        # 初始状态为 pending
        self.jobs[job_id] = JobResult(
            job_id=job_id,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        return job_id

    async def wait_for_result(self, job_id: str, timeout: int = 30) -> JobResult:
        # 模拟 Worker 处理：将状态更新为 success
        job = self.jobs.get(job_id)
        if job:
            job.status = "success"
            job.result = ScreenshotResult(
                image="AAAA",
                image_type="png",
                width=100,
                height=100,
                size_bytes=3,
            )
            # 在模拟中，我们返回带图片的结果（模拟从结果队列获取）
            return job
        return None

    async def update_job_status(self, job_id, status, result=None):
        job = self.jobs.get(job_id)
        if job:
            job.status = status
            if result:
                job.result = result
                # 按照新逻辑：存回持久化状态时移除图片数据
                if job.result.image:
                    job.result.image = None

    async def get_job(self, job_id: str) -> JobResult:
        return self.jobs.get(job_id)


@pytest.fixture
def task_manager():
    return FakeTaskManager()


@pytest.fixture
def handler(task_manager):
    return RpcHandler(task_manager)


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
    assert "get_job_status" in resp["result"]["methods"]
    assert "ping" in resp["result"]["methods"]


# ── screenshot ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_screenshot_sync_response(handler):
    """验证截图请求现在直接返回图像结果，而不是 job_id。"""
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "screenshot",
            "params": {"html": "<h1>Hello</h1>"},
            "id": 3,
        }
    )
    assert "error" not in resp or resp["error"] is None
    assert "image" in resp["result"]
    assert resp["result"]["image"] == "AAAA"
    assert resp["result"]["image_type"] == "png"


@pytest.mark.asyncio
async def test_get_job_status(handler, task_manager):
    """验证查询任务状态返回的是无图结果（用后即焚）。"""
    # 1. 先通过模拟方式注入并更新任务（模拟 Worker 行为）
    job_id = await task_manager.submit_task(ScreenshotParams(html="test"))
    await task_manager.update_job_status(
        job_id,
        "success",
        ScreenshotResult(
            image="AAAA",
            image_type="png",
            width=100,
            height=100,
            size_bytes=3,
        ),
    )

    # 2. 调用 RPC 查询
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "get_job_status",
            "params": {"job_id": job_id},
            "id": 4,
        }
    )
    assert resp["result"]["job_id"] == job_id
    assert resp["result"]["status"] == "success"
    # 按照新逻辑：存回持久化状态时应移除了图片
    assert resp["result"]["result"]["image"] is None
    assert resp["result"]["result"]["image_type"] == "png"


@pytest.mark.asyncio
async def test_get_job_not_found(handler):
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "get_job_status",
            "params": {"job_id": "non-existent-id"},
            "id": 5,
        }
    )
    assert resp["error"]["code"] == ErrorCode.JOB_NOT_FOUND


@pytest.mark.asyncio
async def test_screenshot_missing_html(handler):
    resp = await handler.handle(
        {
            "jsonrpc": "2.0",
            "method": "screenshot",
            "params": {},
            "id": 6,
        }
    )
    assert resp["error"]["code"] == ErrorCode.INVALID_PARAMS


# ── 协议错误 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_method_not_found(handler):
    resp = await handler.handle({"jsonrpc": "2.0", "method": "nope", "id": 7})
    assert resp["error"]["code"] == ErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_parse_error(handler):
    resp = await handler.handle(b"{bad json{{")
    assert resp["error"]["code"] == ErrorCode.PARSE_ERROR


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
