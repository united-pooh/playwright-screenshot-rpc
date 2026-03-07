import pytest

from server.screenshot_service import ScreenshotService


@pytest.mark.asyncio
async def test_mark_request_finished_restarts_browser_at_threshold(monkeypatch):
    service = ScreenshotService()
    service._browser = object()
    service._active_requests = 1
    service._completed_requests_since_restart = 1

    restart_reasons = []

    async def fake_restart(reason: str) -> None:
        restart_reasons.append(reason)

    monkeypatch.setattr(service, "_restart_browser", fake_restart)
    monkeypatch.setattr("server.screenshot_service.settings.BROWSER_RESTART_INTERVAL", 2)

    await service._mark_request_finished()

    assert service._active_requests == 0
    assert restart_reasons == ["已处理 2 次截图请求"]


@pytest.mark.asyncio
async def test_mark_request_finished_skips_restart_when_requests_still_active(monkeypatch):
    service = ScreenshotService()
    service._browser = object()
    service._active_requests = 2
    service._completed_requests_since_restart = 1

    restarted = False

    async def fake_restart(_reason: str) -> None:
        nonlocal restarted
        restarted = True

    monkeypatch.setattr(service, "_restart_browser", fake_restart)
    monkeypatch.setattr("server.screenshot_service.settings.BROWSER_RESTART_INTERVAL", 2)

    await service._mark_request_finished()

    assert service._active_requests == 1
    assert restarted is False
