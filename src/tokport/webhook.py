from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import cast

from .types import WebhookPayload

_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def verify_webhook(
    raw_body: str | bytes,
    headers: Mapping[str, str],
    secret: str,
    *,
    tolerance: int = 300,
    now: int | None = None,
) -> WebhookPayload | None:
    """Verify a TokPort webhook and return its payload, or ``None`` if the signature is not valid.

    The signature is HMAC-SHA256 over ``f"{timestamp}.{raw_body}"`` with the signing secret of the
    API key (https://tokport.io/dashboard/keys). Pass the body exactly as received, before parsing.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    timestamp = lower.get("x-tokport-webhook-timestamp", "")
    signature = lower.get("x-tokport-webhook-signature", "")
    if not timestamp.isdigit() or not _HEX64.match(signature):
        return None
    if abs((now if now is not None else int(time.time())) - int(timestamp)) > tolerance:
        return None
    body = raw_body.encode() if isinstance(raw_body, str) else raw_body
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        return None
    try:
        return cast(WebhookPayload, json.loads(body))
    except ValueError:
        return None
