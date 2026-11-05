from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Callable

import httpx
import pytest

from tokport import AsyncTokPort, TokPort, TokPortError, endpoint_for, infer_task, to_data_url, verify_webhook
from tokport._shared import SseParser

Handler = Callable[[httpx.Request], httpx.Response]


def stub(routes: dict[str, Handler]) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Transport answering from a script keyed by "METHOD /path", recording every request."""
    calls: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        handler = routes.get(f"{req.method} {req.url.path}")
        if handler is None:
            return httpx.Response(404, json={"error": {"message": f"no stub for {req.method} {req.url.path}"}})
        return handler(req)

    return httpx.MockTransport(handle), calls


def client(transport: httpx.MockTransport) -> TokPort:
    return TokPort(api_key="sk-test", base_url="https://api.test", http_client=httpx.Client(transport=transport))


def ok(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> Handler:
    return lambda _req: httpx.Response(status, json=data, headers=headers)


def sse(frames: list[str]) -> Handler:
    return lambda _req: httpx.Response(
        200, content="".join(frames).encode(), headers={"content-type": "text/event-stream"}
    )


def test_task_inference_and_endpoints() -> None:
    assert infer_task("seedream-5-pro", {"prompt": "x"}) == "text-to-image"
    assert infer_task("seedream-5-pro", {"prompt": "x", "image": "data:…"}) == "image-to-image"
    assert infer_task("kling-3-0", {"prompt": "x"}) == "text-to-video"
    assert infer_task("seedance-9-unreleased", {"prompt": "x", "image": "u"}) == "image-to-video"
    # catalog modality beats the name guess: these ids contain "kling" / "wan" but are image models
    assert infer_task("kling-3-0-image", {"prompt": "x"}) == "text-to-image"
    assert infer_task("wan27-image", {"prompt": "x"}) == "text-to-image"
    assert endpoint_for("image-to-image") == "/v1/images/edits"
    assert endpoint_for("image-to-video") == "/v1/videos"
    assert to_data_url(b"\x01\x02", "image/jpeg") == "data:image/jpeg;base64,AQI="


def test_requires_api_key() -> None:
    with pytest.raises(TokPortError):
        TokPort(api_key="")


def test_run_posts_images_with_the_key_and_surfaces_api_errors() -> None:
    transport, calls = stub({"POST /v1/images/generations": ok({"created": 1, "data": [{"url": "https://x/1.png"}]})})
    out = client(transport).run("seedream-5-pro", {"prompt": "a cat", "n": 1})
    assert out["data"][0]["url"] == "https://x/1.png"  # type: ignore[typeddict-item]
    assert calls[0].headers["authorization"] == "Bearer sk-test"
    assert calls[0].headers["user-agent"].startswith("tokport-python/")
    assert json.loads(calls[0].content) == {"model": "seedream-5-pro", "prompt": "a cat", "n": 1}

    failing, _ = stub(
        {
            "POST /v1/images/generations": ok(
                {"error": {"message": "insufficient balance", "code": "quota"}}, 402, {"x-oneapi-request-id": "rid2"}
            )
        }
    )
    with pytest.raises(TokPortError) as exc:
        client(failing).run("seedream-5-pro", {"prompt": "x"})
    assert (exc.value.status, exc.value.code, exc.value.request_id) == (402, "quota", "rid2")
    assert str(exc.value) == "insufficient balance"


def test_run_submits_polls_and_downloads_video() -> None:
    polls = {"n": 0}

    def poll(_req: httpx.Request) -> httpx.Response:
        polls["n"] += 1
        done = polls["n"] >= 2
        return httpx.Response(
            200,
            json={"id": "task_1", "status": "completed" if done else "in_progress", "progress": 100 if done else 50},
        )

    transport, _ = stub(
        {
            "POST /v1/videos": ok({"id": "task_1", "status": "queued"}),
            "GET /v1/videos/task_1": poll,
            "GET /v1/videos/task_1/content": lambda _r: httpx.Response(
                200, content=b"\x01\x02\x03", headers={"content-type": "video/mp4"}
            ),
        }
    )
    out = client(transport).run("seedance-2-0", {"prompt": "waves", "seconds": "5"}, poll_interval=0)
    assert out["content_type"] == "video/mp4"  # type: ignore[typeddict-item]
    assert out["bytes"] == b"\x01\x02\x03"  # type: ignore[typeddict-item]
    assert polls["n"] == 2


def test_run_raises_when_the_video_task_fails() -> None:
    transport, _ = stub(
        {
            "POST /v1/videos": ok({"id": "t"}),
            "GET /v1/videos/t": ok({"id": "t", "status": "failed", "error": {"message": "nsfw", "code": "policy"}}),
        }
    )
    with pytest.raises(TokPortError) as exc:
        client(transport).run("kling-3-0", {"prompt": "x"}, poll_interval=0)
    assert (exc.value.message, exc.value.code) == ("nsfw", "policy")


def test_queue_submit_sends_webhook_priority_and_task() -> None:
    transport, calls = stub(
        {"POST /queue/v1/seedream-5-pro": ok({"status": "IN_QUEUE", "request_id": "req_1", "queue_position": 0})}
    )
    s = client(transport).queue.submit(
        "seedream-5-pro", {"prompt": "x"}, webhook_url="https://hook", priority="low", task="text-to-image"
    )
    assert s["request_id"] == "req_1"
    assert calls[0].url.params["webhook"] == "https://hook"
    assert calls[0].url.params["task"] == "text-to-image"
    assert calls[0].headers["x-tokport-priority"] == "low"
    assert json.loads(calls[0].content) == {"prompt": "x"}


def test_subscribe_streams_status_frames_then_fetches_the_result() -> None:
    frames = [
        'data: {"status":"IN_QUEUE","request_id":"req_1","queue_position":1}\n\n',
        ": keep-alive\n\n",
        'data: {"status":"IN_PROGRESS","request_id":"req_1"}\n\ndata: {"status":"COMPLETED","request_id":"req_1"}\n\n',
    ]
    transport, _ = stub(
        {
            "POST /queue/v1/seedream-5-pro": ok({"status": "IN_QUEUE", "request_id": "req_1"}),
            "GET /queue/v1/seedream-5-pro/requests/req_1/status/stream": sse(frames),
            "GET /queue/v1/seedream-5-pro/requests/req_1": ok(
                {"data": [{"url": "https://x/1.png"}], "request_id": "req_1"}
            ),
        }
    )
    seen: list[str] = []
    out = client(transport).subscribe(
        "seedream-5-pro", {"prompt": "x"}, on_queue_update=lambda s: seen.append(s["status"])
    )
    assert out["request_id"] == "req_1"  # type: ignore[typeddict-item]
    assert seen == ["IN_QUEUE", "IN_QUEUE", "IN_PROGRESS", "COMPLETED"]


def test_subscribe_falls_back_to_polling_and_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tokport._client.time.sleep", lambda _s: None)
    n = {"n": 0}

    def status(_req: httpx.Request) -> httpx.Response:
        n["n"] += 1
        state = "IN_PROGRESS" if n["n"] < 2 else "FAILED"
        return httpx.Response(
            200, json={"status": state, "request_id": "req_2", "error": {"message": "boom", "code": "x"}}
        )

    transport, _ = stub(
        {
            "POST /queue/v1/m": ok({"status": "IN_QUEUE", "request_id": "req_2"}),
            "GET /queue/v1/m/requests/req_2/status/stream": lambda _r: httpx.Response(500, text="nope"),
            "GET /queue/v1/m/requests/req_2/status": status,
        }
    )
    with pytest.raises(TokPortError) as exc:
        client(transport).subscribe("m", {"prompt": "x"})
    assert exc.value.message == "boom"


def test_result_raises_pending_on_202_and_cancel_uses_put() -> None:
    transport, calls = stub(
        {
            "GET /queue/v1/m/requests/req_3": ok({"status": "IN_PROGRESS", "request_id": "req_3"}, 202),
            "PUT /queue/v1/m/requests/req_3/cancel": ok({"status": "CANCELLATION_REQUESTED", "request_id": "req_3"}),
        }
    )
    c = client(transport)
    with pytest.raises(TokPortError) as exc:
        c.queue.result("m", "req_3")
    assert exc.value.code == "pending"
    assert c.queue.cancel("m", "req_3")["request_id"] == "req_3"
    assert calls[-1].method == "PUT"


def test_models_list_hits_the_site_without_the_key() -> None:
    transport, calls = stub({"GET /api/models": ok({"models": [{"id": "a"}]})})
    c = TokPort(api_key="sk-test", site_url="https://site.test", http_client=httpx.Client(transport=transport))
    assert c.models.list(modality="video", available=True)[0]["id"] == "a"
    assert str(calls[0].url) == "https://site.test/api/models?modality=video&available=1"
    assert "authorization" not in calls[0].headers


def test_sse_parser_handles_frames_split_across_chunks() -> None:
    p = SseParser()
    out: list[Any] = []
    for chunk in ['data: {"a":1}\n\nda', 'ta: {"a":2}\n', "\n", 'data: {"a":3}\r\n\r\n']:
        out.extend(p.feed(chunk))
    assert out == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_verify_webhook_accepts_a_valid_signature_and_rejects_tampering() -> None:
    secret = "whsec_test"
    body = json.dumps({"request_id": "req_1", "gateway_request_id": "g", "status": "OK", "payload": {"ok": True}})
    ts = "1700000000"
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    headers = {"X-TokPort-Webhook-Timestamp": ts, "x-tokport-webhook-signature": sig}
    payload = verify_webhook(body, headers, secret, now=1700000010)
    assert payload is not None and payload["request_id"] == "req_1"
    assert verify_webhook(body.encode(), headers, secret, now=1700000010) is not None
    assert verify_webhook(body + " ", headers, secret, now=1700000010) is None
    assert verify_webhook(body, headers, secret, now=1700009999) is None
    assert verify_webhook(body, headers, "other", now=1700000010) is None


async def test_async_client_runs_and_subscribes() -> None:
    frames = ['data: {"status":"COMPLETED","request_id":"r"}\n\n']
    routes = {
        "POST /v1/images/generations": ok({"data": [{"url": "u"}]}),
        "POST /queue/v1/m": ok({"status": "IN_QUEUE", "request_id": "r"}),
        "GET /queue/v1/m/requests/r/status/stream": sse(frames),
        "GET /queue/v1/m/requests/r": ok({"data": [{"url": "q"}], "request_id": "r"}),
    }

    def handle(req: httpx.Request) -> httpx.Response:
        return routes[f"{req.method} {req.url.path}"](req)

    seen: list[str] = []

    async def on_update(s: Any) -> None:
        seen.append(s["status"])

    async with AsyncTokPort(
        api_key="sk-test",
        base_url="https://api.test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ) as c:
        image = await c.run("gpt-image-2", {"prompt": "x"})
        assert image["data"][0]["url"] == "u"  # type: ignore[typeddict-item]
        out = await c.subscribe("m", {"prompt": "x"}, on_queue_update=on_update)
        assert out["request_id"] == "r"  # type: ignore[typeddict-item]
    assert seen == ["IN_QUEUE", "COMPLETED"]
