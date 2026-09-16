"""Anthropic Claude 原生协议适配器(OpenAI <-> Claude 互转)"""
import json
import time
import uuid

from .base import BaseAdapter, AdapterError, UpstreamRequest
from .openai_compat import USAGE_SENTINEL

STOP_REASON_MAP = {
    "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _convert_messages(openai_messages):
    """OpenAI messages -> (system_text, anthropic_messages, tools)"""
    system_parts = []
    messages = []
    for msg in openai_messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str)
                                     else " ".join(p.get("text", "") for p in content))
            continue
        if role == "tool":
            # OpenAI 工具结果 -> anthropic user 消息中的 tool_result 块
            block = {"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""),
                     "content": content if isinstance(content, str)
                     else [{"type": "text", "text": p.get("text", "")} for p in content]}
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"].append(block)
            else:
                messages.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            blocks = []
            if isinstance(content, str):
                if content:
                    blocks.append({"type": "text", "text": content})
            elif content:
                for p in content:
                    if p.get("type") == "text":
                        blocks.append({"type": "text", "text": p.get("text", "")})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.get("id", uuid.uuid4().hex),
                               "name": fn.get("name", ""), "input": args})
            if not blocks:
                blocks.append({"type": "text", "text": ""})
            messages.append({"role": "assistant", "content": blocks})
            continue
        # user
        if isinstance(content, str):
            messages.append({"role": "user", "content": [{"type": "text", "text": content}] if content
                             else [{"type": "text", "text": ""}]})
        else:
            blocks = []
            for p in content or []:
                if p.get("type") == "text":
                    blocks.append({"type": "text", "text": p.get("text", "")})
                elif p.get("type") == "image_url":
                    url = (p.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        # data:image/png;base64,xxxx
                        try:
                            mime, b64 = url.split(",", 1)
                            media_type = mime.split(":")[1].split(";")[0]
                            blocks.append({"type": "image", "source": {
                                "type": "base64", "media_type": media_type, "data": b64}})
                        except (ValueError, IndexError):
                            pass
            messages.append({"role": "user", "content": blocks or [{"type": "text", "text": ""}]})
    return "\n\n".join(system_parts), messages


def _convert_tools(tools):
    out = []
    for t in tools or []:
        if t.get("type") == "function":
            fn = t.get("function", {})
            out.append({"name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _anthropic_to_openai_message(up):
    content_blocks = up.get("content") or []
    texts, tool_calls = [], []
    for b in content_blocks:
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "tool_use":
            tool_calls.append({
                "id": b.get("id", ""), "type": "function",
                "function": {"name": b.get("name", ""),
                             "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}})
    message = {"role": "assistant", "content": "".join(texts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
        if message["content"] is None:
            message["content"] = ""
    return {
        "id": "chatcmpl-" + (up.get("id") or uuid.uuid4().hex),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": up.get("model", ""),
        "choices": [{
            "index": 0, "message": message, "finish_reason":
                STOP_REASON_MAP.get(up.get("stop_reason"), "stop"),
        }],
        "usage": {
            "prompt_tokens": (up.get("usage") or {}).get("input_tokens", 0),
            "completion_tokens": (up.get("usage") or {}).get("output_tokens", 0),
            "total_tokens": ((up.get("usage") or {}).get("input_tokens", 0)
                             + (up.get("usage") or {}).get("output_tokens", 0)),
        },
    }


class AnthropicAdapter(BaseAdapter):
    name = "anthropic"

    def build_request(self, channel, api_key, model, kind, openai_body):
        if kind != self.KIND_CHAT:
            raise AdapterError("Anthropic 适配器仅支持 chat/completions")
        system, messages = _convert_messages(openai_body.get("messages") or [])
        body = {"model": model, "messages": messages,
                "max_tokens": int(openai_body.get("max_tokens") or 4096)}
        if system:
            body["system"] = system
        for k in ("temperature", "top_p", "stop"):
            if openai_body.get(k) is not None:
                body["stop_sequences" if k == "stop" else k] = openai_body[k]
        tools = _convert_tools(openai_body.get("tools"))
        if tools:
            body["tools"] = tools
            if openai_body.get("tool_choice"):
                tc = openai_body["tool_choice"]
                if tc.get("type") == "function":
                    body["tool_choice"] = {"type": "tool", "name": tc["function"].get("name")}
                elif tc.get("type") == "required":
                    body["tool_choice"] = {"type": "any"}
                elif tc.get("type") == "auto":
                    body["tool_choice"] = {"type": "auto"}
        stream = bool(openai_body.get("stream"))
        if stream:
            body["stream"] = True
        headers = {"Content-Type": "application/json",
                   "x-api-key": api_key, "anthropic-version": "2023-06-01"}
        base = self._clean_base_url(channel.base_url)
        ver = self._version_prefix(channel)
        return UpstreamRequest(base + ver + "/messages", headers, body, stream)

    def adapt_response(self, upstream_json, kind):
        return _anthropic_to_openai_message(upstream_json or {})

    @staticmethod
    def extract_usage(upstream_json):
        u = (upstream_json or {}).get("usage") or {}
        return {"prompt_tokens": u.get("input_tokens", 0),
                "completion_tokens": u.get("output_tokens", 0)}

    @staticmethod
    def extract_token_details(upstream_json):
        u = (upstream_json or {}).get("usage") or {}
        return {"cache_read": int(u.get("cache_read_input_tokens") or 0),
                "cache_creation": int(u.get("cache_creation_input_tokens") or 0)}

    def models_request(self, channel, api_key):
        base = self._clean_base_url(channel.base_url)
        ver = self._version_prefix(channel)
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        return base + ver + "/models", headers

    def transform_stream(self, sse_lines):
        """anthropic SSE 事件流 -> openai chunk 流"""
        msg_id = "chatcmpl-" + uuid.uuid4().hex
        model = ""
        created = int(time.time())
        usage = {}
        tool_index = -1  # 当前 tool_use 块序号
        text_started = False

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
            etype = ev.get("type")
            if etype == "message_start":
                m = ev.get("message") or {}
                model = m.get("model", "")
                u = m.get("usage") or {}
                usage["prompt_tokens"] = u.get("input_tokens", 0)
                usage["cache_read"] = int(u.get("cache_read_input_tokens") or 0)
                usage["cache_creation"] = int(u.get("cache_creation_input_tokens") or 0)
                if not text_started:
                    yield chunk({"role": "assistant", "content": ""})
                    text_started = True
            elif etype == "content_block_start":
                b = ev.get("content_block") or {}
                if b.get("type") == "tool_use":
                    tool_index += 1
                    yield chunk({"tool_calls": [{
                        "index": tool_index, "id": b.get("id", ""), "type": "function",
                        "function": {"name": b.get("name", ""), "arguments": ""}}]})
            elif etype == "content_block_delta":
                d = ev.get("delta") or {}
                if d.get("type") == "text_delta":
                    yield chunk({"content": d.get("text", "")})
                elif d.get("type") == "input_json_delta":
                    yield chunk({"tool_calls": [{
                        "index": max(tool_index, 0), "function": {
                            "arguments": d.get("partial_json", "")}}]})
            elif etype == "message_delta":
                u = ev.get("usage") or {}
                if u.get("output_tokens") is not None:
                    usage["completion_tokens"] = u.get("output_tokens", 0)
                finish = STOP_REASON_MAP.get((ev.get("delta") or {}).get("stop_reason"), "stop")
                yield chunk({}, finish=finish)
            elif etype == "message_stop":
                yield "[DONE]"
        if usage:
            yield USAGE_SENTINEL, usage
