from __future__ import annotations

import json

import httpx
import pytest

from app.services.llm.qwen_client import QwenClient, QwenResponseParseError, QwenResponseValidationError


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status_code: int = 200):
        self._lines = lines
        self.status_code = status_code
        self.text = "\n".join(lines)
        self._request = httpx.Request("POST", "https://example.com/chat/completions")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self) -> bytes:
        return self.text.encode()

    @property
    def request(self):
        return self._request

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self._request, text=self.text)
            raise httpx.HTTPStatusError("stream failed", request=self._request, response=response)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """Minimal httpx.AsyncClient stub that records the last streamed payload."""

    def __init__(self, *args, lines: list[str] | None = None, payload_sink: list[dict] | None = None, **kwargs):
        self._lines = lines or []
        self._payload_sink = payload_sink if payload_sink is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method: str, url: str, *, headers=None, json=None, content=None):
        # Support both json= (old path) and content= (SSE path sends raw bytes)
        if content is not None:
            try:
                payload = json_mod_loads(content)
            except Exception:
                payload = {}
        else:
            payload = json or {}
        self._payload_sink.append({"method": method, "url": url, "headers": headers or {}, "payload": payload})
        return _FakeStreamResponse(self._lines)


def json_mod_loads(data) -> dict:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8")
    return json.loads(data)


@pytest.mark.asyncio
async def test_chat_stream_text_yields_sse_delta_content(monkeypatch):
    payloads: list[dict] = []
    lines = [
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[],"usage":{"total_tokens":12}}',
        "data: [DONE]",
    ]

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient(*args, lines=lines, payload_sink=payloads, **kwargs)

    monkeypatch.setattr("app.services.llm.transport.httpx.AsyncClient", _client_factory)

    client = QwenClient(api_key="test-key", base_url="https://example.com/v1", model="qwen3-omni-flash")
    chunks = [chunk async for chunk in client.chat_stream_text(messages=[{"role": "user", "content": "你好"}])]

    assert chunks == ["你", "好"]
    assert len(payloads) == 1
    assert payloads[0]["payload"]["stream"] is True
    assert payloads[0]["payload"]["stream_options"] == {"include_usage": True}
    assert payloads[0]["payload"]["messages"] == [{"role": "user", "content": "你好"}]


@pytest.mark.asyncio
async def test_collect_sse_response_aggregates_chunks_and_usage(monkeypatch):
    """_collect_sse_response should concatenate delta.content and return final usage."""
    payloads: list[dict] = []
    lines = [
        'data: {"choices":[{"delta":{"content":"{"}}]}',
        'data: {"choices":[{"delta":{"content":"\\"ok\\": true}"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}',
        "data: [DONE]",
    ]

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient(*args, lines=lines, payload_sink=payloads, **kwargs)

    monkeypatch.setattr("app.services.llm.transport.httpx.AsyncClient", _client_factory)

    client = QwenClient(api_key="test-key", base_url="https://example.com/v1", model="qwen3-omni-flash")
    text, usage = await client._collect_sse_response(
        {"model": "qwen3-omni-flash", "messages": [], "modalities": ["text"], "enable_thinking": False},
        timeout_seconds=30,
    )

    # stream=True and stream_options should be injected automatically
    sent = payloads[0]["payload"]
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert sent["modalities"] == ["text"]
    assert sent["enable_thinking"] is False

    assert '{"ok": true}' in text
    assert usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


@pytest.mark.asyncio
async def test_chat_json_omni_with_images_uses_streaming_no_json_schema(monkeypatch, tmp_path):
    """chat_json for qwen3-omni-flash + image_paths must use SSE and not send json_schema."""
    # Create a tiny fake image file
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff")  # minimal JPEG magic bytes

    payloads: list[dict] = []
    response_json = '{"image_summaries": [{"image_idx": 0, "summary": "A product on a white background"}]}'
    lines = [
        f'data: {{"choices":[{{"delta":{{"content":{json.dumps(response_json)}}}}}]}}',
        'data: {"choices":[],"usage":{"prompt_tokens":50,"completion_tokens":20,"total_tokens":70}}',
        "data: [DONE]",
    ]

    def _client_factory(*args, **kwargs):
        return _FakeAsyncClient(*args, lines=lines, payload_sink=payloads, **kwargs)

    monkeypatch.setattr("app.services.llm.transport.httpx.AsyncClient", _client_factory)

    client = QwenClient(api_key="test-key", base_url="https://example.com/v1", model="qwen3-omni-flash")
    schema = {
        "name": "image_analysis",
        "schema": {
            "type": "object",
            "properties": {"image_summaries": {"type": "array"}},
            "required": ["image_summaries"],
        },
    }
    result, usage = await client.chat_json(
        system_prompt="Analyze images.",
        user_prompt="Describe each image.",
        image_paths=[str(img)],
        response_schema=schema,
    )

    sent = payloads[0]["payload"]
    # Must use streaming
    assert sent["stream"] is True
    # Must NOT send response_format (json_schema incompatible with omni+images)
    assert "response_format" not in sent
    # Must include omni-required params
    assert sent["modalities"] == ["text"]
    assert sent["enable_thinking"] is False
    # Schema hint must be embedded in system prompt instead
    system_msg = sent["messages"][0]["content"]
    assert "image_summaries" in system_msg
    # Result should parse correctly
    assert "image_summaries" in result
    assert usage["total_tokens"] == 70


@pytest.mark.asyncio
async def test_chat_json_non_omni_model_uses_standard_path(monkeypatch):
    """Non-omni models must go through _post_chat_completions (non-streaming)."""
    called_post = []

    async def _fake_post(self_inner, payload, *, timeout_seconds, max_retries=2):
        called_post.append(payload)
        return {
            "choices": [{"message": {"content": '{"result": 1}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }

    monkeypatch.setattr(QwenClient, "_post_chat_completions", _fake_post)

    client = QwenClient(api_key="k", base_url="https://example.com/v1", model="qwen-plus")
    result, usage = await client.chat_json(
        system_prompt="sys",
        user_prompt="prompt",
    )

    assert result == {"result": 1}
    assert len(called_post) == 1
    # No stream key should be injected for the standard path
    assert "stream" not in called_post[0]


def test_qwen_client_extracts_json_object_from_common_model_formats():
    assert QwenClient._extract_json_object('{"ok": true}') == {"ok": True}
    assert QwenClient._extract_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert QwenClient._extract_json_object('解析结果如下：\n{"ok": true, "items": []}\n请确认。') == {
        "ok": True,
        "items": [],
    }


def test_qwen_client_json_extract_rejects_invalid_content():
    with pytest.raises(QwenResponseParseError) as err:
        QwenClient._extract_json_object("这里没有 JSON")

    assert err.value.code == "llm_parse_failed"


def test_qwen_client_lightweight_schema_validation_reports_missing_required_field():
    schema = {
        "name": "sample",
        "schema": {
            "type": "object",
            "properties": {
                "shots": {"type": "array"},
                "voice_design": {"type": "object"},
            },
            "required": ["shots", "voice_design"],
        },
    }

    with pytest.raises(QwenResponseValidationError) as err:
        QwenClient._validate_response_schema({"shots": []}, schema)

    assert err.value.code == "llm_validation_failed"
    assert "voice_design" in str(err.value)
