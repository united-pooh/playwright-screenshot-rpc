import time
from typing import Any, Optional

import pytest

from server.models import JobStatus, ScreenshotResult
from server.worker import Worker


class FakeScreenshotService:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def screenshot(self, params: Any) -> ScreenshotResult:
        self.calls.append(params)
        return ScreenshotResult(
            image="AAAA",
            image_type="png",
            width=100,
            height=100,
            size_bytes=3,
        )


class FakeTaskManager:
    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self._tasks: list[dict[str, Any]] = list(tasks)
        self.status_updates: list[
            tuple[str, JobStatus, Optional[ScreenshotResult]]
        ] = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def pop_task(self, timeout: int = 5):  # noqa: ARG002
        if self._tasks:
            return self._tasks.pop(0)
        return None

    async def update_job_status(
        self,
        job_id: str,
        status: JobStatus,
        result: Optional[ScreenshotResult] = None,
    ) -> None:
        self.status_updates.append((job_id, status, result))


def _task(job_id: str = "job-1") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "params": {"html": "<h1>Hello</h1>"},
    }


@pytest.mark.asyncio
async def test_worker_does_not_restart_when_thresholds_disabled(monkeypatch):
    service = FakeScreenshotService()
    task_manager = FakeTaskManager([_task()])
    worker = Worker(service=service, task_manager=task_manager)

    async def pop_task(timeout: int = 5):  # noqa: ARG001
        if task_manager._tasks:
            return task_manager._tasks.pop(0)
        assert worker.should_exit is False
        worker.should_exit = True
        return None

    monkeypatch.setattr(task_manager, "pop_task", pop_task)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_TASKS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_AGE_SECONDS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_RSS_MB", 0)

    await worker.run()

    assert worker._completed_tasks == 1
    assert [status for _, status, _ in task_manager.status_updates] == [
        "processing",
        "success",
    ]
    assert len(service.calls) == 1


@pytest.mark.asyncio
async def test_worker_restarts_after_task_result_when_task_limit_reached(monkeypatch):
    service = FakeScreenshotService()
    task_manager = FakeTaskManager([_task()])
    worker = Worker(service=service, task_manager=task_manager)

    monkeypatch.setattr("server.worker.settings.WORKER_MAX_TASKS", 1)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_AGE_SECONDS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_RSS_MB", 0)

    await worker.run()

    assert worker.should_exit is True
    assert worker._completed_tasks == 1
    assert [status for _, status, _ in task_manager.status_updates] == [
        "processing",
        "success",
    ]


@pytest.mark.asyncio
async def test_worker_restarts_on_idle_when_age_limit_reached(monkeypatch):
    worker = Worker(
        service=FakeScreenshotService(),
        task_manager=FakeTaskManager([]),
    )
    worker._started_at = time.monotonic() - 2

    monkeypatch.setattr("server.worker.settings.WORKER_MAX_TASKS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_AGE_SECONDS", 1)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_RSS_MB", 0)

    await worker.run()

    assert worker.should_exit is True
    assert worker._completed_tasks == 0


@pytest.mark.asyncio
async def test_worker_restarts_on_idle_when_rss_limit_reached(monkeypatch):
    worker = Worker(
        service=FakeScreenshotService(),
        task_manager=FakeTaskManager([]),
    )

    monkeypatch.setattr("server.worker.settings.WORKER_MAX_TASKS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_AGE_SECONDS", 0)
    monkeypatch.setattr("server.worker.settings.WORKER_MAX_RSS_MB", 750)
    monkeypatch.setattr(worker, "_read_rss_mb", lambda: 800.0)

    await worker.run()

    assert worker.should_exit is True
    assert worker._completed_tasks == 0
