from __future__ import annotations

import builtins
import time
from collections.abc import Iterator
from typing import Any, Callable, Union, cast
from urllib.parse import quote

import httpx

from . import _shared as s
from ._models import ModelId
from ._shared import TokPortError
from .types import ImageResult, Input, ModelInfo, Priority, QueueStatus, TaskKind, VideoResult, VideoRunResult

Model = Union[ModelId, str]


class TokPort:
    """Client for the TokPort image & video generation API (https://tokport.io).

    >>> client = TokPort(api_key="sk-...")
    >>> image = client.run("gpt-image-2", {"prompt": "a neon koi", "size": "1024x1024"})
    >>> image["data"][0]["url"]
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = s.DEFAULT_BASE_URL,
        queue_url: str | None = None,
        site_url: str = s.DEFAULT_SITE_URL,
        timeout: float = 120.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise TokPortError("Provide api_key (get one at https://tokport.io/dashboard/keys)", 0, "config")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.queue_url = (queue_url or base_url).rstrip("/")
        self.site_url = site_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=timeout)
        self.queue = Queue(self)
        self.models = Models(self)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TokPort:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _url(self, base: str, path: str) -> str:
        return {"api": self.base_url, "queue": self.queue_url, "site": self.site_url}[base] + path

    def _request(
        self, method: str, base: str, path: str, *, json: Any = None, extra: dict[str, str] | None = None
    ) -> Any:
        hdrs = s.headers(self.api_key, extra) if base != "site" else {"user-agent": s.USER_AGENT}
        res = self._http.request(method, self._url(base, path), json=json, headers=hdrs)
        return s.parse(res)

    def run(
        self,
        model: Model,
        input: Input,
        *,
        task: TaskKind | None = None,
        poll_interval: float = 5.0,
        timeout: float = s.DEFAULT_TIMEOUT_S,
    ) -> ImageResult | VideoRunResult:
        """Synchronous call. Images answer inline; videos submit, poll until done and download the clip."""
        endpoint = s.endpoint_for(task or s.infer_task(model, input))
        body = {"model": model, **input}
        if endpoint != "/v1/videos":
            return cast(ImageResult, self._request("POST", "api", endpoint, json=body))
        submitted = self._request("POST", "api", "/v1/videos", json=body)
        task_path = "/v1/videos/" + quote(submitted["id"], safe="")
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(poll_interval)
            status = self._request("GET", "api", task_path)
            if status.get("status") == "completed":
                res = self._http.get(self._url("api", task_path + "/content"), headers=s.headers(self.api_key))
                if res.is_error:
                    raise TokPortError(f"content download failed ({res.status_code})", res.status_code, "content")
                return cast(
                    VideoRunResult,
                    {**status, "bytes": res.content, "content_type": res.headers.get("content-type", "video/mp4")},
                )
            if status.get("status") == "failed":
                raise s.video_failed(status)
            if time.monotonic() > deadline:
                raise TokPortError("timed out waiting for the video", 408, "timeout")

    def subscribe(
        self,
        model: Model,
        input: Input,
        *,
        task: TaskKind | None = None,
        webhook_url: str | None = None,
        priority: Priority | None = None,
        logs: bool = False,
        on_queue_update: Callable[[QueueStatus], None] | None = None,
        timeout: float = s.DEFAULT_TIMEOUT_S,
    ) -> ImageResult | VideoResult:
        """Submit to the queue, follow status (server-sent events, falling back to polling), return the result."""
        submitted = self.queue.submit(model, input, task=task, webhook_url=webhook_url, priority=priority)
        if on_queue_update:
            on_queue_update(submitted)
        deadline = time.monotonic() + timeout
        last: QueueStatus = submitted
        try:
            for status in self.queue.stream(model, submitted["request_id"], logs=logs):
                last = status
                if on_queue_update:
                    on_queue_update(status)
                if status["status"] in s.TERMINAL:
                    break
                if time.monotonic() > deadline:
                    raise TokPortError("timed out waiting for the request", 408, "timeout")
        except TokPortError as err:
            if err.code == "timeout":
                raise
        except httpx.HTTPError:
            pass
        # stream unavailable or cut short: poll instead
        while last["status"] in s.PENDING:
            if time.monotonic() > deadline:
                raise TokPortError("timed out waiting for the request", 408, "timeout")
            time.sleep(2.0)
            last = self.queue.status(model, submitted["request_id"], logs=logs)
            if on_queue_update:
                on_queue_update(last)
        s.raise_for_terminal(last)
        return cast(Union[ImageResult, VideoResult], self.queue.result(model, submitted["request_id"]))


class Queue:
    """The queue API: submit, follow and fetch long-running requests."""

    def __init__(self, client: TokPort) -> None:
        self._c = client

    def submit(
        self,
        model: Model,
        input: Input,
        *,
        task: TaskKind | None = None,
        webhook_url: str | None = None,
        priority: Priority | None = None,
    ) -> QueueStatus:
        extra: dict[str, str] | None = {"x-tokport-priority": priority} if priority else None
        path = s.queue_path(model, task=task, webhook=webhook_url)
        return cast(QueueStatus, self._c._request("POST", "queue", path, json=input, extra=extra))

    def status(self, model: str, request_id: str, *, logs: bool = False) -> QueueStatus:
        path = s.queue_path(model, "requests", request_id, "status", logs="1" if logs else None)
        return cast(QueueStatus, self._c._request("GET", "queue", path))

    def stream(self, model: str, request_id: str, *, logs: bool = False) -> Iterator[QueueStatus]:
        """Status frames until a terminal state (server-sent events)."""
        path = s.queue_path(model, "requests", request_id, "status", "stream", logs="1" if logs else None)
        hdrs = s.headers(self._c.api_key, {"accept": "text/event-stream"})
        with self._c._http.stream("GET", self._c._url("queue", path), headers=hdrs, timeout=None) as res:
            if res.is_error:
                raise TokPortError(f"stream unavailable ({res.status_code})", res.status_code, "stream")
            parser = s.SseParser()
            for chunk in res.iter_text():
                for frame in parser.feed(chunk):
                    yield cast(QueueStatus, frame)

    def result(self, model: str, request_id: str, *, timeout: int | None = None) -> Any:
        """The result; waits up to ``timeout`` seconds server-side (max 60) before answering with the status."""
        path = s.queue_path(model, "requests", request_id, timeout=timeout)
        return s.check_result(self._c._request("GET", "queue", path))

    def cancel(self, model: str, request_id: str) -> dict[str, str]:
        path = s.queue_path(model, "requests", request_id, "cancel")
        return cast(dict[str, str], self._c._request("PUT", "queue", path))


class Models:
    """The public model catalog (ids, prices, endpoints)."""

    def __init__(self, client: TokPort) -> None:
        self._c = client

    def list(
        self,
        *,
        q: str | None = None,
        modality: str | None = None,
        vendor: str | None = None,
        task: TaskKind | None = None,
        available: bool = False,
    ) -> builtins.list[ModelInfo]:
        body = self._c._request("GET", "site", s.models_path(q, modality, vendor, task, available))
        return cast(list[ModelInfo], body["models"])
