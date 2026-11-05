"""Response shapes returned by the TokPort API (https://tokport.io/docs)."""

from __future__ import annotations

import sys
from typing import Any, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:  # pragma: no cover
    from typing_extensions import NotRequired

TaskKind = Literal["text-to-image", "image-to-image", "text-to-video", "image-to-video"]
QueueState = Literal["IN_QUEUE", "IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED"]
Priority = Literal["normal", "low"]

Input = dict[str, Any]


class ErrorInfo(TypedDict):
    message: str
    code: NotRequired[str]


class LogLine(TypedDict):
    timestamp: str
    message: str


class QueueStatus(TypedDict):
    status: QueueState
    request_id: str
    response_url: str
    status_url: str
    cancel_url: str
    queue_position: NotRequired[int]
    gateway_request_id: NotRequired[str]
    logs: NotRequired[list[LogLine]]
    metrics: NotRequired[dict[str, float]]
    error: NotRequired[ErrorInfo]


class ImageData(TypedDict, total=False):
    url: str
    b64_json: str
    revised_prompt: str


class ImageResult(TypedDict):
    data: list[ImageData]
    created: NotRequired[int]
    request_id: NotRequired[str]


class VideoFile(TypedDict):
    url: str
    content_type: str


class Artifact(TypedDict):
    key: str
    type: str
    content_type: str
    url: str


class VideoResult(TypedDict):
    """Queue result for a video request."""

    request_id: str
    task_id: str
    status: Literal["completed"]
    video: VideoFile | None
    artifacts: list[Artifact]
    seconds: NotRequired[str]
    size: NotRequired[str]


class VideoRunResult(TypedDict):
    """Result of ``run()`` for a video model: the task object plus the downloaded clip."""

    id: str
    status: Literal["completed"]
    progress: int
    bytes: bytes
    content_type: str
    model: NotRequired[str]
    created_at: NotRequired[int]
    completed_at: NotRequired[int]


class Pricing(TypedDict):
    price: float
    unit: Literal["image", "second"]
    currency: Literal["USD"]


class ModelEndpoint(TypedDict):
    task: TaskKind
    path: str


class ModelUrls(TypedDict):
    page: str
    playground: str
    api: str
    openapi: str
    llms_txt: str


class ModelInfo(TypedDict):
    id: str
    display_name: str
    vendor: str
    modality: Literal["image", "video"]
    category: TaskKind | None
    tasks: list[TaskKind]
    tags: list[str]
    description: str
    status: Literal["active", "catalog"]
    pricing: Pricing | None
    added: str
    endpoints: list[ModelEndpoint]
    urls: ModelUrls
    thumbnail_url: str | None


class WebhookPayload(TypedDict):
    request_id: str
    gateway_request_id: str
    status: Literal["OK", "ERROR"]
    payload: NotRequired[Any]
    error: NotRequired[ErrorInfo]
