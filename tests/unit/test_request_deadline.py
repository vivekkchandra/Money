import asyncio
from types import SimpleNamespace

from money.api.app import RequestBoundary
from money.api.settings import Settings


def boundary(app):
    settings = Settings(
        money_env="test",
        database_url="sqlite:///:memory:",
        money_allow_unauthenticated_dev=True,
        money_request_timeout_seconds=0.1,
    )
    return RequestBoundary(app, SimpleNamespace(state=SimpleNamespace(settings=settings)))


def test_slow_request_body_is_cancelled_before_business_logic():
    reached, sent = [], []

    async def app(scope, receive, send):
        reached.append(True)

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"secret", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(boundary(app)({"type": "http", "path": "/research", "headers": []}, receive, send))
    assert not reached
    assert sent[0]["status"] == 408
    assert b"REQUEST_TIMEOUT" in sent[1]["body"] and b"secret" not in sent[1]["body"]


def test_streamed_chunks_replay_one_bounded_body_not_an_unbounded_event_list():
    received, sent = [], []
    count = 0

    async def app(scope, receive, send):
        received.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        nonlocal count
        count += 1
        return {"type": "http.request", "body": b"x", "more_body": count < 100}

    async def send(message):
        sent.append(message)

    asyncio.run(boundary(app)({"type": "http", "path": "/research", "headers": []}, receive, send))
    assert received == [{"type": "http.request", "body": b"x" * 100, "more_body": False}]
    assert sent[0]["status"] == 204
