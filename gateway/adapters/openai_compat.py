"""OpenAI 兼容适配器:OpenAI / DeepSeek / Kimi / 通义 / 智谱 / OpenRouter / Groq / 硅基 / Ollama 等"""
import json

from .base import BaseAdapter, UpstreamRequest

USAGE_SENTINEL = "__usage__"


class OpenAICompatAdapter(BaseAdapter):
    name = "openai_compat"

    def build_request(self, channel, api_key, model, kind, openai_body):
        base = (channel.base_url or "").rstrip("/")
        body = dict(openai_body)
        body["model"] = model
        stream = bool(body.get("stream"))
        if stream and "stream_options" not in body:
            # 让上游在最后一个 chunk 带 usage,便于精确计费
            body["stream_options"] = {"include_usage": True}

        if kind == self.KIND_CHAT:
            path = "/v1/chat/completions"
        elif kind == self.KIND_COMPLETIONS:
            path = "/v1/completions"
        elif kind == self.KIND_EMBEDDINGS:
            path = "/v1/embeddings"
            body.pop("stream", None)
            stream = False
        elif kind == self.KIND_IMAGES:
            path = "/v1/images/generations"
            body.pop("stream", None)
            stream = False
        else:
            path = "/v1/chat/completions"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        return UpstreamRequest(base + path, headers, body, stream)

    def transform_stream(self, sse_lines):
        """openai 兼容流几乎直通,只透传并抓取 usage chunk(含缓存命中)"""
        usage = {}
        for data in sse_lines:
            if not data:
                continue
            try:
                chunk = json.loads(data)
            except (ValueError, TypeError):
                yield data
                continue
            if isinstance(chunk, dict) and chunk.get("usage"):
                u = chunk["usage"]
                usage = {
                    "prompt_tokens": u.get("prompt_tokens", 0),
                    "completion_tokens": u.get("completion_tokens", 0),
                    "cache_read": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
                    "cache_creation": 0,
                }
            yield data
        if usage:
            yield USAGE_SENTINEL, usage
