import asyncio
from typing import Any, cast

import pytest

from server.main import (
    WORKER_MANAGER_APP_KEY,
    WorkerSubprocessManager,
    _on_cleanup,
    _on_startup,
    build_app,
)
from server.task_manager import TaskManager


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminate_called = False
        self.kill_called = False
        self._wait_future: asyncio.Future[int] = (
            asyncio.get_running_loop().create_future()
        )

    async def wait(self) -> int:
        return await asyncio.shield(self._wait_future)

    def terminate(self) -> None:
        self.terminate_called = True
        self.finish(-15)

    def kill(self) -> None:
        self.kill_called = True
        self.finish(-9)

    def finish(self, returncode: int) -> None:
        self.returncode = returncode
        if not self._wait_future.done():
            self._wait_future.set_result(returncode)


class FakeTaskManager(TaskManager):
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.asyncio
async def test_worker_subprocess_manager_starts_and_stops_child(
    monkeypatch,
) -> None:
    commands: list[tuple[str, ...]] = []
    process = FakeProcess(pid=1234)

    async def fake_create_subprocess_exec(*cmd: str) -> FakeProcess:
        commands.append(cmd)
        return process

    monkeypatch.setattr(
        "server.main.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    manager = WorkerSubprocessManager(
        python_executable="/tmp/python",
        restart_delay_seconds=0,
    )

    await manager.start()
    await manager.stop()

    assert commands == [("/tmp/python", "-m", "server.worker")]
    assert process.terminate_called is True


@pytest.mark.asyncio
async def test_worker_subprocess_manager_restarts_child_after_unexpected_exit(
    monkeypatch,
) -> None:
    real_sleep = asyncio.sleep
    commands: list[tuple[str, ...]] = []
    processes = [FakeProcess(pid=111), FakeProcess(pid=222)]

    async def fake_create_subprocess_exec(*cmd: str) -> FakeProcess:
        commands.append(cmd)
        return processes.pop(0)

    async def fast_sleep(_seconds: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(
        "server.main.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    monkeypatch.setattr("server.main.asyncio.sleep", fast_sleep)

    manager = WorkerSubprocessManager(restart_delay_seconds=0)
    await manager.start()

    first_process = cast(FakeProcess, manager._process)
    assert first_process is not None
    first_process.finish(1)

    await real_sleep(0)
    await real_sleep(0)

    assert len(commands) == 2
    assert manager._process is not None
    assert manager._process.pid == 222

    await manager.stop()


@pytest.mark.asyncio
async def test_startup_and_cleanup_manage_worker_when_enabled(
    monkeypatch,
) -> None:
    task_manager = FakeTaskManager()
    app = build_app(task_manager)
    created_managers: list[Any] = []

    class FakeWorkerManager:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False
            created_managers.append(self)

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr("server.main.settings.AUTO_START_WORKER", True)
    monkeypatch.setattr("server.main.WorkerSubprocessManager", FakeWorkerManager)

    await _on_startup(app)

    assert task_manager.connected is True
    assert len(created_managers) == 1
    assert created_managers[0].started is True
    assert app[WORKER_MANAGER_APP_KEY] is created_managers[0]

    await _on_cleanup(app)

    assert created_managers[0].stopped is True
    assert task_manager.disconnected is True
