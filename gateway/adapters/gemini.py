"""Google Gemini 原生协议适配器(OpenAI <-> Gemini 互转)"""
import json
import time
import uuid

from .base import BaseAdapter, AdapterError, UpstreamRequest
from .openai_compat import USAGE_SENTINEL

FINISH_MAP = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "content_filter",
              "RECITATION": "content_filter", "OTHER": "stop"}


def _convert_contents(openai_messages):
    system_parts = []
    contents = []
    last_tool_name = ""
    for msg in openai_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str)
                                     else " ".join(p.get("text", "") for p in content))
            continue
        if role == "tool":
            try:
                resp = json.loads(content) if isinstance(content, str) else (content or {})
            except (ValueError, TypeError):
                resp = {"result": content}
            contents.append({"role": "user", "parts": [
                {"functionResponse": {"name": msg.get("name") or last_tool_name or "tool",
                                      "response": resp}}]})
            continue
        parts = []
        if isinstance(content, str):
            if content:
                parts.append({"text": content})
        else:
            for p in content or []:
                if p.get("type") == "text":
                    parts.append({"text": p.get("text", "")})
                elif p.get("type") == "image_url":
                    url = (p.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        try:
                            mime, b64 = url.split(",", 1)
                            parts.append({"inlineData": {"mimeType": mime.split(":")[1].split(";")[0],
                                                         "data": b64}})
                        except (ValueError, IndexError):
                            pass
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            parts.append({"functionCall": {"name": fn.get("name", ""), "args": args}})
            last_tool_name = fn.get("name", "")
        if parts:
            contents.append({"role": "model" if role == "assistant" else "user", "parts": parts})
    return system_parts, contents


def _gemini_to_openai(up, model=""):
    cands = up.get("candidates") or [{}]
    c0 = cands[0] or {}
    parts = ((c0.get("content") or {}).get("parts")) or []
    texts = [p.get("text", "") for p in parts if "text" in p]
    tool_calls = []
    for i, p in enumerate(parts):
        fc = p.get("functionCall")
        if fc:
            tool_calls.append({"id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                               "function": {"name": fc.get("name", ""),
                                            "arguments": json.dumps(fc.get("args") or {},
                                                                    ensure_ascii=False)}})
    message = {"role": "assistant", "content": "".join(texts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    u = up.get("usageMetadata") or {}
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion", "created": int(time.time()), "model": model or up.get("model", ""),
        "choices": [{"index": 0, "message": message,
                     "finish_reason": FINISH_MAP.get(c0.get("finishReason"), "stop")}],
        "usage": {"prompt_tokens": u.get("promptTokenCount", 0),
                  "completion_tokens": u.get("candidatesTokenCount", 0),
                  "total_tokens": u.get("totalTokenCount", 0)},
    }


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def build_request(self, channel, api_key, model, kind, openai_body):
        base = self._clean_base_url(channel.base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = api_key

        if kind == self.KIND_EMBEDDINGS:
            # OpenAI /v1/embeddings {input, model} -> gemini embedContent
            inp = openai_body.get("input", "")
            texts = inp if isinstance(inp, list) else [inp]
            if len(texts) == 1:
                url = f"{base}/v1beta/models/{model}:embedContent"
                body = {"content": {"parts": [{"text": str(texts[0])}]}}
                return UpstreamRequest(url, headers, body, False)
            url = f"{base}/v1beta/models/{model}:batchEmbedContents"
            body = {"requests": [{"model": f"models/{model}",
                                  "content": {"parts": [{"text": str(t)}]}} for t in texts]}
            return UpstreamRequest(url, headers, body, False)

        if kind != self.KIND_CHAT:
            raise AdapterError("Gemini 适配器仅支持 chat/completions 与 embeddings")

        system_parts, contents = _convert_contents(openai_body.get("messages") or [])
        body = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        gen_cfg = {}
        if openai_body.get("temperature") is not None:
            gen_cfg["temperature"] = openai_body["temperature"]
        if openai_body.get("top_p") is not None:
            gen_cfg["topP"] = openai_body["top_p"]
        if openai_body.get("max_tokens") is not None:
            gen_cfg["maxOutputTokens"] = int(openai_body["max_tokens"])
        if openai_body.get("stop"):
            gen_cfg["stopSequences"] = openai_body["stop"]
        if gen_cfg:
            body["generationConfig"] = gen_cfg
        tools = openai_body.get("tools") or []
        decls = [t.get("function") for t in tools if t.get("type") == "function"]
        if decls:
            body["tools"] = [{"functionDeclarations": decls}]
        stream = bool(openai_body.get("stream"))
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return UpstreamRequest(f"{base}/v1beta/models/{model}:{method}", headers, body, stream)

    def adapt_response(self, upstream_json, kind):
        if kind == self.KIND_EMBEDDINGS:
            if "embedding" in upstream_json:  # 单条
                return {"object": "list", "data": [{"embedding": upstream_json["embedding"].get("values", []),
                                                   "index": 0}], "model": "", "usage": {}}
            embs = upstream_json.get("embeddings") or []  # 批量
            return {"object": "list",
                    "data": [{"embedding": e.get("values", []), "index": i}
                             for i, e in enumerate(embs)], "model": "", "usage": {}}
        return _gemini_to_openai(upstream_json or {})

    @staticmethod
    def extract_usage(upstream_json):
        u = (upstream_json or {}).get("usageMetadata") or {}
        return {"prompt_tokens": u.get("promptTokenCount", 0),
                "completion_tokens": u.get("candidatesTokenCount", 0)}

    @staticmethod
    def extract_token_details(upstream_json):
        u = (upstream_json or {}).get("usageMetadata") or {}
        return {"cache_read": int(u.get("cachedContentTokenCount") or 0),
                "cache_creation": 0}

    def models_request(self, channel, api_key):
        base = self._clean_base_url(channel.base_url)
        headers = {}
        if api_key:
            headers["x-goog-api-key"] = api_key
        return base + "/v1beta/models?pageSize=1000", headers

    @staticmethod
    def parse_models(data):
        out = []
        for m in (data.get("models") or []):
            name = (m.get("name") or "").removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            if name and any(k in methods for k in ("generateContent", "embedContent")):
                out.append(name)
        return out

    @staticmethod
    def parse_models_meta(data):
        """Gemini 模型列表自带 inputTokenLimit / outputTokenLimit"""
        out = {}
        for m in (data.get("models") or []):
            name = (m.get("name") or "").removeprefix("models/")
            if not name:
                continue
            meta = {}
            try:
                if m.get("inputTokenLimit"):
                    meta["context"] = int(m["inputTokenLimit"])
                if m.get("outputTokenLimit"):
                    meta["max_output"] = int(m["outputTokenLimit"])
            except (TypeError, ValueError):
                pass
            if meta:
                out[name] = meta
        return out

    def transform_stream(self, sse_lines):
        """gemini ?alt=sse 流 -> openai chunk 流"""
        msg_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        model = ""
        usage = {}
        role_sent = False

        def chunk(delta, finish=None):
            return json.dumps({
                "id": msg_id, "object": "chat.completion.chunk", "created": created,
                "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }, ensure_ascii=False)

        for data in sse_lines:
            if not data:
                continue
            try:
                ev = json.loads(data)
            except (ValueError, TypeError):
                continue
            model = ev.get("modelVersion", "") or model
            u = ev.get("usageMetadata") or {}
            if u.get("promptTokenCount"):
                usage.setdefault("prompt_tokens", u["promptTokenCount"])
            if u.get("candidatesTokenCount"):
                usage["completion_tokens"] = u["candidatesTokenCount"]
            if u.get("cachedContentTokenCount"):
                usage["cache_read"] = int(u["cachedContentTokenCount"])
            cands = ev.get("candidates") or []
            if not cands:
                continue
            c0 = cands[0] or {}
            parts = ((c0.get("content") or {}).get("parts")) or []
            for p in parts:
                if not role_sent:
                    yield chunk({"role": "assistant", "content": ""})
                    role_sent = True
                if "text" in p:
                    yield chunk({"content": p.get("text", "")})
                elif "functionCall" in p:
                    fc = p["functionCall"]
                    yield chunk({"tool_calls": [{
                        "index": 0, "id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                        "function": {"name": fc.get("name", ""),
                                     "arguments": json.dumps(fc.get("args") or {},
                                                             ensure_ascii=False)}}]})
            finish = FINISH_MAP.get(c0.get("finishReason"))
            if finish:
                yield chunk({}, finish=finish)
        yield "[DONE]"
        if usage:
            yield USAGE_SENTINEL, usage
