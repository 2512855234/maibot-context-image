import base64
import json
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from context_image.clients.images_api import ImagesApiClient
from context_image.errors import ConfigurationError, UpstreamError
from context_image.models import (
    GenerationMode,
    GenerationPlan,
    GenerationRequest,
    ImageReference,
    ReferenceRole,
    Subject,
)
from context_image.parsing import detect_image_mime, parse_image_payload
from context_image.validation import validate_public_https_url


VALID_PNG = b"\x89PNG\r\n\x1a\ncontent"
VALID_PNG_B64 = base64.b64encode(VALID_PNG).decode("ascii")


def api_config(**overrides):
    values = {
        "base_url": "https://api.openai.com/v1",
        "api_key": "test-key",
        "model": "gpt-image-2",
        "generations_path": "/images/generations",
        "edits_path": "/images/edits",
        "timeout_seconds": 30.0,
        "size": "1024x1536",
        "quality": "high",
        "output_format": "png",
        "background": "auto",
        "moderation": "auto",
        "max_retries": 2,
        "max_output_bytes": 1024 * 1024,
        "block_private_networks": True,
        "allowed_image_hosts": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def generation_plan() -> GenerationPlan:
    request = GenerationRequest.from_mapping({"request": "画一只猫"})
    return GenerationPlan(
        request=request,
        mode=GenerationMode.TEXT_TO_IMAGE,
        subject=Subject.OTHER,
        final_prompt="一只在月球喝咖啡的橘猫",
        references=(),
        operation="generation",
    )


def edit_plan() -> GenerationPlan:
    request = GenerationRequest.from_mapping(
        {"request": "把背景改成下雪夜晚", "subject": "bot"}
    )
    base = ImageReference(
        "base", VALID_PNG + b"base", "image/png", "base", ReferenceRole.BASE
    )
    identity = ImageReference(
        "identity",
        VALID_PNG + b"identity",
        "image/png",
        "identity:bot",
        ReferenceRole.IDENTITY,
    )
    return GenerationPlan(
        request=request,
        mode=GenerationMode.IMAGE_TO_IMAGE,
        subject=Subject.BOT,
        final_prompt="保持身份，把背景改成下雪夜晚",
        references=(base, identity),
        operation="edit",
    )


class ParsingTests(TestCase):
    def test_parses_base64_payload(self):
        kind, value = parse_image_payload(
            {"data": [{"b64_json": VALID_PNG_B64}]}
        )

        self.assertEqual(kind, "base64")
        self.assertEqual(value, VALID_PNG_B64)

    def test_detects_supported_magic_headers(self):
        self.assertEqual(detect_image_mime(VALID_PNG), "image/png")
        self.assertEqual(detect_image_mime(b"\xff\xd8\xffjpeg"), "image/jpeg")
        self.assertEqual(
            detect_image_mime(b"RIFF\x00\x00\x00\x00WEBPdata"),
            "image/webp",
        )

    def test_rejects_private_and_non_https_urls(self):
        with self.assertRaises(UpstreamError):
            validate_public_https_url("http://example.com/image.png")
        with self.assertRaises(UpstreamError):
            validate_public_https_url("https://127.0.0.1/image.png")


class ImagesApiTests(IsolatedAsyncioTestCase):
    async def test_missing_api_key_fails_before_request(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(api_config(api_key=""), http)

        with self.assertRaises(ConfigurationError):
            await client.generate(generation_plan())

        self.assertEqual(calls, 0)

    async def test_generation_posts_json_and_parses_base64(self):
        captured = []

        def handler(request):
            captured.append(request)
            return httpx.Response(
                200,
                json={"data": [{"b64_json": VALID_PNG_B64}]},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(api_config(), http)

        result = await client.generate(generation_plan())

        payload = json.loads(captured[0].content)
        self.assertEqual(captured[0].url.path, "/v1/images/generations")
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["prompt"], "一只在月球喝咖啡的橘猫")
        self.assertEqual(result.data, VALID_PNG)
        self.assertEqual(result.mime_type, "image/png")

    async def test_edit_uses_repeated_image_fields_in_plan_order(self):
        captured_body = []

        async def handler(request):
            captured_body.append(await request.aread())
            return httpx.Response(
                200,
                json={"data": [{"b64_json": VALID_PNG_B64}]},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(api_config(), http)

        await client.generate(edit_plan())

        body = captured_body[0]
        self.assertLess(
            body.find(b'filename="base.png"'),
            body.find(b'filename="identity.png"'),
        )
        self.assertEqual(body.count(b'name="image[]"'), 2)
        self.assertNotIn(b'name="image"\r\n', body)

    async def test_authentication_error_is_not_retried_or_leaked(self):
        calls = 0

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                401,
                json={"error": {"message": "bad placeholder credential"}},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(api_config(api_key="placeholder-credential"), http)

        with self.assertRaises(UpstreamError) as caught:
            await client.generate(generation_plan())

        self.assertEqual(calls, 1)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn("placeholder-credential", str(caught.exception))

    async def test_retryable_status_retries_then_succeeds(self):
        calls = 0
        sleeps = []

        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={"data": [{"b64_json": VALID_PNG_B64}]},
            )

        async def fake_sleep(seconds):
            sleeps.append(seconds)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(api_config(), http, sleep=fake_sleep)

        result = await client.generate(generation_plan())

        self.assertEqual(result.data, VALID_PNG)
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [0.0])

    async def test_url_result_downloads_validated_image(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"data": [{"url": "https://cdn.example/image.png"}]},
                )
            return httpx.Response(200, content=VALID_PNG)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        validated = []
        client = ImagesApiClient(
            api_config(),
            http,
            url_validator=lambda url: validated.append(url),
        )

        result = await client.generate(generation_plan())

        self.assertEqual(validated, ["https://cdn.example/image.png"])
        self.assertEqual(result.data, VALID_PNG)

    async def test_fake_ip_result_is_allowed_when_private_blocking_is_disabled(self):
        def handler(request):
            if request.method == "POST":
                return httpx.Response(
                    200,
                    json={"data": [{"url": "https://198.18.1.124/image.png"}]},
                )
            return httpx.Response(200, content=VALID_PNG)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http.aclose)
        client = ImagesApiClient(
            api_config(block_private_networks=False),
            http,
        )

        try:
            result = await client.generate(generation_plan())
        except UpstreamError as exc:
            self.fail(f"关闭私网阻断后不应拒绝代理 Fake-IP: {exc}")

        self.assertEqual(result.data, VALID_PNG)

