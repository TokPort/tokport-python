# TokPort Python SDK

[![PyPI](https://img.shields.io/pypi/v/tokport.svg)](https://pypi.org/project/tokport/)
[![Python](https://img.shields.io/pypi/pyversions/tokport.svg)](https://pypi.org/project/tokport/)
[![CI](https://github.com/TokPort/tokport-python/actions/workflows/ci.yml/badge.svg)](https://github.com/TokPort/tokport-python/actions/workflows/ci.yml)

The official Python client for **[TokPort](https://tokport.io/?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python)**, the pay-as-you-go API for image and video generation. One API key gives you GPT Image, Nano Banana, Seedream, Seedance, Kling, Qwen Image, Wan and [30+ more models](https://tokport.io/models?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python).

- Sync calls: images come back inline. For videos the client submits the task, polls it and downloads the clip
- A queue with live status (server-sent events, with polling as a fallback), webhooks and cancellation
- Sync `TokPort` and async `AsyncTokPort` clients, fully typed, one dependency (`httpx`)

```bash
pip install tokport
```

Get an API key at **[tokport.io/dashboard/keys](https://tokport.io/dashboard/keys?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python)**. New accounts can [sign up here](https://tokport.io/register?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python).

## Quickstart

```python
import os
from tokport import TokPort

client = TokPort(api_key=os.environ["TOKPORT_API_KEY"])

# Image: answers inline
image = client.run("gpt-image-2", {"prompt": "a neon koi in a rainy alley", "size": "1024x1024"})
print(image["data"][0]["url"])

# Video: submits, polls and downloads the MP4 for you
video = client.run("seedance-2-0-fast", {"prompt": "waves crashing at night", "seconds": "5"})
open("waves.mp4", "wb").write(video["bytes"])
```

## Queue: progress updates, webhooks, cancellation

```python
result = client.subscribe(
    "kling-3-0",
    {"prompt": "a paper boat drifting down a stream", "seconds": "5"},
    on_queue_update=lambda s: print(s["status"], s.get("queue_position")),
)
print(result["video"]["url"])

# Or step by step
req = client.queue.submit("seedream-5-pro", {"prompt": "a travel poster of Kyoto"}, webhook_url="https://example.com/hook")
for status in client.queue.stream("seedream-5-pro", req["request_id"]):
    print(status["status"])
out = client.queue.result("seedream-5-pro", req["request_id"])
client.queue.cancel("seedream-5-pro", req["request_id"])  # if you change your mind
```

## Async

```python
import asyncio
from tokport import AsyncTokPort

async def main() -> None:
    async with AsyncTokPort(api_key="sk-...") as client:
        image = await client.run("nano-banana-3-pro", {"prompt": "studio portrait, soft light"})
        print(image["data"][0]["url"])

asyncio.run(main())
```

## Image inputs (edits, image-to-video)

Pass an image URL, or turn local bytes into a data URL:

```python
from tokport import to_data_url

photo = to_data_url(open("me.jpg", "rb").read(), "image/jpeg")
edited = client.run("seedream-5-pro", {"prompt": "make it a watercolor", "image": photo})
clip = client.run("kling-3-0", {"prompt": "she turns and smiles", "image": photo, "seconds": "5"})
```

## Verify webhooks

```python
from tokport import verify_webhook

payload = verify_webhook(raw_body, request.headers, os.environ["TOKPORT_WEBHOOK_SECRET"])
if payload is None:
    return Response(status=400)
```

## Models

`tokport.ModelId` lists the model ids that were live when this release was built, so editors can autocomplete them. Any newer id from the [model catalog](https://tokport.io/models?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) works too. You can also query the catalog at runtime:

```python
for m in client.models.list(modality="video"):
    print(m["id"], m["pricing"])
```

| Image | Video |
| --- | --- |
| [GPT Image 2](https://tokport.io/models/gpt-image-2?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) | [Seedance 2.5](https://tokport.io/models/seedance-2-5?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) |
| [Nano Banana 3 Pro](https://tokport.io/models/nano-banana-3-pro?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) | [Seedance 2.0 Fast](https://tokport.io/models/seedance-2-0-fast?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) |
| [Seedream 5 Pro](https://tokport.io/models/seedream-5-pro?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) | [Kling 3.0](https://tokport.io/models/kling-3-0?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) |
| [Qwen Image 3.0](https://tokport.io/models/qwen-image-v3?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) | [Wan 3.0 Video](https://tokport.io/models/wan3-video?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) |
| [Grok Image 2.0](https://tokport.io/models/grok-image-2-0?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) | [HappyHorse 1.1](https://tokport.io/models/happyhorse-1-1?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python) |

Live per-model rates: [tokport.io/pricing](https://tokport.io/pricing?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python).

## Errors

API errors raise `tokport.TokPortError` with `.status`, `.code`, `.message` and `.request_id` (include the request id when you contact support).

## Already using the OpenAI SDK?

TokPort is OpenAI-compatible, so you can also just point the OpenAI SDK at it:

```python
from openai import OpenAI

client = OpenAI(base_url="https://api.tokport.io/v1", api_key="sk-...")
client.images.generate(model="seedream-5-pro", prompt="a lighthouse at dusk")
```

## Links

- Docs: [tokport.io/docs](https://tokport.io/docs?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python)
- Models: [tokport.io/models](https://tokport.io/models?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python)
- Pricing: [tokport.io/pricing](https://tokport.io/pricing?utm_source=pypi&utm_medium=readme&utm_campaign=tokport-python)
- Other SDKs: [TypeScript](https://github.com/TokPort/tokport-js) · [Go](https://github.com/TokPort/tokport-go)

## License

MIT
