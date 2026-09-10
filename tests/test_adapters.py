"""适配器协议转换单元测试(离线,不需要真实上游)"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.adapters.anthropic import AnthropicAdapter
from gateway.adapters.gemini import GeminiAdapter
from gateway.adapters.registry import get_adapter

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


class FakeChannel:
    id = 1
    base_url = "https://api.example.com"
    proxy_url = ""
    timeout = 0
    api_key = "sk-test"
    azure_api_version = "2024-10-21"
    model_mapping = {}


# ================= Anthropic =================
print("== Anthropic: 请求转换 ==")
ad = AnthropicAdapter()
req = ad.build_request(FakeChannel(), "sk-x", "claude-sonnet-4-5", "chat", {
    "model": "m", "messages": [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "继续"}],
    "max_tokens": 100, "temperature": 0.5, "stream": False})
check("url", req.url == "https://api.example.com/v1/messages", req.url)
check("header x-api-key", req.headers.get("x-api-key") == "sk-x")
check("header version", req.headers.get("anthropic-version") == "2023-06-01")
body = req.json_body
check("system", body.get("system") == "你是助手")
check("messages len", len(body["messages"]) == 3)
check("roles", [m["role"] for m in body["messages"]] == ["user", "assistant", "user"])
check("max_tokens", body["max_tokens"] == 100)

print("== Anthropic: 工具调用转换 ==")
req = ad.build_request(FakeChannel(), "sk-x", "c", "chat", {
    "model": "m", "messages": [{"role": "user", "content": "天气"}],
    "tools": [{"type": "function", "function": {"name": "get_weather",
               "description": "d", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]})
check("tools", req.json_body.get("tools", [{}])[0].get("name") == "get_weather")

print("== Anthropic: 响应转换 ==")
up = {"id": "msg_1", "model": "claude", "stop_reason": "end_turn",
      "content": [{"type": "text", "text": "回答内容"}],
      "usage": {"input_tokens": 10, "output_tokens": 5}}
oa = ad.adapt_response(up, "chat")
check("content", oa["choices"][0]["message"]["content"] == "回答内容")
check("finish", oa["choices"][0]["finish_reason"] == "stop")
check("usage", oa["usage"]["prompt_tokens"] == 10 and oa["usage"]["completion_tokens"] == 5)

print("== Anthropic: 流式转换 ==")
events = [
    json.dumps({"type": "message_start", "message": {"model": "claude", "usage": {"input_tokens": 7}}}),
    json.dumps({"type": "content_block_start", "content_block": {"type": "text"}}),
    json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你好"}}),
    json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "世界"}}),
    json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}),
    json.dumps({"type": "message_stop"}),
]
out = list(ad.transform_stream(events))
texts = []
usage = None
for item in out:
    if isinstance(item, tuple):
        usage = item[1]; continue
    try:
        j = json.loads(item)
        d = (j.get("choices") or [{}])[0].get("delta") or {}
        if d.get("content"): texts.append(d["content"])
    except ValueError:
        pass
check("stream text", "".join(texts) == "你好世界", texts)
check("stream done", out[-2] == "[DONE]" or "[DONE]" in out)
check("stream usage", usage == {"prompt_tokens": 7, "completion_tokens": 3,
                                "cache_read": 0, "cache_creation": 0}, usage)

# ================= Gemini =================
print("== Gemini: 请求转换 ==")
gad = GeminiAdapter()
req = gad.build_request(FakeChannel(), "g-key", "gemini-2.5-flash", "chat", {
    "model": "m", "messages": [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "go"}],
    "temperature": 0.7, "max_tokens": 88, "stream": False})
check("url", req.url.endswith("/v1beta/models/gemini-2.5-flash:generateContent"), req.url)
check("header key", req.headers.get("x-goog-api-key") == "g-key")
gb = req.json_body
check("systemInstruction", gb["systemInstruction"]["parts"][0]["text"] == "sys")
check("contents", len(gb["contents"]) == 3)
check("roles", [c["role"] for c in gb["contents"]] == ["user", "model", "user"])
check("genConfig", gb["generationConfig"]["maxOutputTokens"] == 88)

print("== Gemini: 流式 url ==")
req = gad.build_request(FakeChannel(), "g", "gemini-2.5-flash", "chat", {
    "model": "m", "messages": [{"role": "user", "content": "x"}], "stream": True})
check("stream url", req.url.endswith(":streamGenerateContent?alt=sse"), req.url)

print("== Gemini: 响应转换 ==")
up = {"candidates": [{"content": {"parts": [{"text": "Gemini回复"}]}, "finishReason": "STOP"}],
      "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 6, "totalTokenCount": 10}}
oa = gad.adapt_response(up, "chat")
check("content", oa["choices"][0]["message"]["content"] == "Gemini回复")
check("usage", oa["usage"]["prompt_tokens"] == 4 and oa["usage"]["completion_tokens"] == 6)

print("== Gemini: embeddings 转换 ==")
req = gad.build_request(FakeChannel(), "g", "text-embedding-004", "embeddings",
                        {"model": "m", "input": "hello"})
check("embed url", req.url.endswith(":embedContent"), req.url)
oa = gad.adapt_response({"embedding": {"values": [0.1, 0.2]}}, "embeddings")
check("embed data", oa["data"][0]["embedding"] == [0.1, 0.2])

print("== Gemini: 流式转换 ==")
events = [
    json.dumps({"candidates": [{"content": {"parts": [{"text": "第一"}]}}]}),
    json.dumps({"candidates": [{"content": {"parts": [{"text": "段"}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 9}}),
]
out = list(gad.transform_stream(events))
texts = []
for i in out:
    if isinstance(i, tuple): continue
    if i == "[DONE]": continue
    d = json.loads(i)["choices"][0]["delta"].get("content", "")
    if d: texts.append(d)
check("stream text", "".join(texts) == "第一段", texts)
check("stream done", "[DONE]" in out)

# ================= Azure =================
print("== Azure ==")
aad = get_adapter("azure")
req = aad.build_request(FakeChannel(), "az-key", "my-deploy", "chat",
                        {"model": "x", "messages": [{"role": "user", "content": "hi"}], "stream": True})
check("url", "deployments/my-deploy/chat/completions" in req.url, req.url)
check("api-version", "api-version=2024-10-21" in req.url)
check("header", req.headers.get("api-key") == "az-key")
check("model removed from body", "model" not in req.json_body)

# ================= openai_compat =================
print("== openai_compat ==")
oad = get_adapter("openai_compat")
req = oad.build_request(FakeChannel(), "sk-1", "deepseek-chat", "chat",
                        {"model": "x", "messages": [], "stream": True})
check("url", req.url == "https://api.example.com/v1/chat/completions")
check("stream_options", req.json_body.get("stream_options") == {"include_usage": True})
check("bearer", req.headers.get("Authorization") == "Bearer sk-1")
req = oad.build_request(FakeChannel(), "sk-1", "d", "embeddings", {"model": "x", "input": "a"})
check("embed no stream", "stream" not in req.json_body)

# ================= 缓存 token 解析 =================
print("== 缓存 token 解析 ==")
from gateway.adapters.anthropic import AnthropicAdapter as _AA
from gateway.adapters.gemini import GeminiAdapter as _GA
d = _AA.extract_token_details({"usage": {"cache_read_input_tokens": 100,
                                         "cache_creation_input_tokens": 50}})
check("anthropic cache", d == {"cache_read": 100, "cache_creation": 50}, d)
d = _GA.extract_token_details({"usageMetadata": {"cachedContentTokenCount": 88}})
check("gemini cache", d == {"cache_read": 88, "cache_creation": 0}, d)
from gateway.adapters.base import BaseAdapter as _BA
d = _BA.extract_token_details({"usage": {"prompt_tokens_details": {"cached_tokens": 42}}})
check("openai cache", d == {"cache_read": 42, "cache_creation": 0}, d)

# ================= auto 模型路由 =================
print("== auto 模型路由 ==")
import app as _app_mod
from gateway.relay import _auto_candidates as _ac
class _AK:
    allowed_models = []
class _AK2:
    allowed_models = ["m1", "m3"]
with _app_mod.app.app_context():
    from gateway.models import Setting, Channel
    from gateway.db import db as _db
    Channel.query.delete()
    _db.session.add(Channel(name="mock", base_url="http://127.0.0.1:9999",
                            models=["test-model", "test-model-pro"], enabled=True))
    _db.session.query(Setting).filter(Setting.key == 'auto_models').delete()
    _db.session.commit()
    check("auto 全部渠道", _ac(_AK()) == ["test-model", "test-model-pro"], _ac(_AK()))
    check("auto 白名单", _ac(_AK2()) == ["m1", "m3"], _ac(_AK2()))
    Setting.set("auto_models", "m2, m1")
    check("auto 偏好+白名单过滤", _ac(_AK2()) == ["m1"], _ac(_AK2()))
    check("auto 偏好无白名单", _ac(_AK()) == ["m2", "m1"], _ac(_AK()))
    Setting.set("auto_models", "")
    Channel.query.delete()
    _db.session.commit()

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
