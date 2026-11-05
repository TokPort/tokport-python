from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from ._models import MODEL_MODALITY
from ._version import __version__
from .types import Input, TaskKind

DEFAULT_BASE_URL = "https://api.tokport.io"
DEFAULT_SITE_URL = "https://tokport.io"
DEFAULT_TIMEOUT_S = 600.0

USER_AGENT = f"tokport-python/{__version__}"

_VIDEO_HINT = re.compile(r"video|seedance|kling|hailuo|wan|sora|veo|vidu|jimeng|minimax", re.I)
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")
PENDING = ("IN_QUEUE", "IN_PROGRESS")


class TokPortError(Exception):
    """An error answered by the TokPort API, or raised by the client while waiting on it."""

    def __init__(
        self,
        message: str,
        status: int,
        code: str = "error",
        body: Any = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.body = body
        self.request_id = request_id

    def __repr__(self) -> str:
        return f"TokPortError(status={self.status}, code={self.code!r}, message={self.message!r})"


def _has_image(input: Input) -> bool:
    return bool(input.get("image")) or bool(input.get("input_reference")) or bool(input.get("images"))


def infer_task(model: str, input: Input) -> TaskKind:
    """Task for a sync call: the model's modality from the catalog, else a guess from its id."""
    modality = MODEL_MODALITY.get(model)
    if modality == "video" or (modality is None and _VIDEO_HINT.search(model)):
        return "image-to-video" if _has_image(input) else "text-to-video"
    return "image-to-image" if _has_image(input) else "text-to-image"


def endpoint_for(task: TaskKind) -> str:
    if task.endswith("video"):
        return "/v1/videos"
    return "/v1/images/edits" if task == "image-to-image" else "/v1/images/generations"


def to_data_url(data: bytes, mime: str = "image/png") -> str:
    """Turn raw bytes into a ``data:`` URL for image inputs."""
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def queue_path(model: str, *parts: str, **params: Any) -> str:
    path = "/queue/v1/" + quote(model, safe="")
    for part in parts:
        path += "/" + part
    qs = urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
    return f"{path}?{qs}" if qs else path


def models_path(q: str | None, modality: str | None, vendor: str | None, task: str | None, available: bool) -> str:
    qs = urlencode(
        {k: v for k, v in {"q": q, "modality": modality, "vendor": vendor, "task": task}.items() if v}
        | ({"available": "1"} if available else {})
    )
    return f"/api/models?{qs}" if qs else "/api/models"


def headers(api_key: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    h = {"authorization": f"Bearer {api_key}", "user-agent": USER_AGENT}
    if extra:
        h.update(extra)
    return h


def parse(res: httpx.Response) -> Any:
    """JSON body of a response, raising ``TokPortError`` for API errors (202 is not one)."""
    text = res.text
    try:
        body: Any = json.loads(text) if text else None
    except ValueError:
        body = text
    if res.is_error:
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        raise TokPortError(
            err.get("message") or f"HTTP {res.status_code}",
            res.status_code,
            err.get("code") or f"http_{res.status_code}",
            body,
            res.headers.get("x-oneapi-request-id"),
        )
    return body


def check_result(body: Any) -> Any:
    if isinstance(body, dict) and body.get("status") in PENDING:
        raise TokPortError(f"request is still {body['status']}", 202, "pending", body)
    return body


def raise_for_terminal(status: Mapping[str, Any]) -> None:
    if status.get("status") == "FAILED":
        err = status.get("error") or {}
        raise TokPortError(
            err.get("message") or "request failed",
            422,
            err.get("code") or "failed",
            status,
            status.get("gateway_request_id"),
        )
    if status.get("status") == "CANCELLED":
        raise TokPortError("request was cancelled", 410, "cancelled", status)


def video_failed(status: Mapping[str, Any]) -> TokPortError:
    err = status.get("error") or {}
    return TokPortError(err.get("message") or "video task failed", 422, err.get("code") or "task_failed", status)


class SseParser:
    """Minimal server-sent-events reader: feed text, get the JSON of each ``data:`` frame."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> Iterator[Any]:
        self._buffer += chunk.replace("\r\n", "\n")
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            lines: list[str] = [ln[5:].lstrip() for ln in frame.split("\n") if ln.startswith("data:")]
            if lines:
                yield json.loads("\n".join(lines))
