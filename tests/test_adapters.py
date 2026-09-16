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

# ================= 客户端 IP 提取 =================
print("== 客户端 IP 提取 ==")
from gateway import relay as _relay
from flask import Flask as _Flask
_app2 = _Flask(__name__)
with _app2.test_request_context(headers={"X-Real-IP": "1.2.3.4",
                                         "X-Forwarded-For": "9.9.9.9, 8.8.8.8",
                                         "User-Agent": "ua-test"}):
    _ip, _ua = _relay._client_info()
    check("X-Real-IP 优先", _ip == "1.2.3.4", _ip)
    check("User-Agent 提取", _ua == "ua-test", _ua)
with _app2.test_request_context(headers={"X-Forwarded-For": "9.9.9.9, 8.8.8.8"}):
    _ip, _ua = _relay._client_info()
    check("X-Forwarded-For 回退", _ip == "9.9.9.9", _ip)
with _app2.test_request_context(environ_overrides={"REMOTE_ADDR": "203.0.113.5"}):
    _ip, _ua = _relay._client_info()
    check("remote_addr 回退", _ip == "203.0.113.5", _ip)

# ================= _read_request_text 多端点支持 =================
print("== _read_request_text 多端点支持 ==")
_chat_body = {"messages": [{"role": "user", "content": "你好"}]}
check("chat messages 提取", "你好" in _relay._read_request_text(_chat_body))
_comp_body = {"prompt": "complete this"}
check("completions prompt 提取", "complete this" in _relay._read_request_text(_comp_body))
_emb_body = {"input": "embed this text"}
check("embeddings input 提取", "embed this text" in _relay._read_request_text(_emb_body))
_emb_list = {"input": ["text1", "text2"]}
check("embeddings input 列表", "text1" in _relay._read_request_text(_emb_list) and "text2" in _relay._read_request_text(_emb_list))
_img_body = {"prompt": "a cat"}
check("images prompt 提取", "a cat" in _relay._read_request_text(_img_body))
_empty = _relay._read_request_text({})
check("空 body 不崩溃", _empty == "")

# ================= base_url /v1 后缀去重 =================
print("== base_url /v1 去重 ==")
from gateway.adapters.base import BaseAdapter as _BA
check("无 /v1 不变", _BA._clean_base_url("https://api.com") == "https://api.com")
check("去除 /v1", _BA._clean_base_url("https://api.com/v1") == "https://api.com")
check("去除 /v1/", _BA._clean_base_url("https://api.com/v1/") == "https://api.com")
check("空字符串", _BA._clean_base_url("") == "")
check("None 安全", _BA._clean_base_url(None) == "")
check("中间 /v1 不误删", _BA._clean_base_url("https://api.com/v1beta") == "https://api.com/v1beta")

# 验证 openai_compat 适配器拼接不会重复 /v1
from gateway.adapters.openai_compat import OpenAICompatAdapter as _OI
_oi_inst = _OI()
_ch = type("MockCh", (), {"base_url": "https://api.openai.com/v1", "adapter": "openai_compat",
                           "api_key": "sk-x", "real_model": lambda self, m: m})()
_req = _oi_inst.build_request(_ch, "sk-x", "gpt-4", "chat", {"messages": []})
check("build_request 去除 /v1 后缀", _req.url == "https://api.openai.com/v1/chat/completions", _req.url)
_ch2 = type("MockCh", (), {"base_url": "https://api.openai.com", "adapter": "openai_compat",
                            "api_key": "sk-x", "real_model": lambda self, m: m})()
_req2 = _oi_inst.build_request(_ch2, "sk-x", "gpt-4", "chat", {"messages": []})
check("build_request 无 /v1 正常", _req2.url == "https://api.openai.com/v1/chat/completions", _req2.url)

# ================= 自定义 api_version (如 v4) =================
print("== 自定义 api_version ==")
_ch_v4 = type("MockCh", (), {"base_url": "https://api.example.com", "adapter": "openai_compat",
                              "api_key": "sk-x", "real_model": lambda self, m: m,
                              "api_version": "v4"})()
_req_v4 = _oi_inst.build_request(_ch_v4, "sk-x", "gpt-4", "chat", {"messages": []})
check("v4 版本路径", _req_v4.url == "https://api.example.com/v4/chat/completions", _req_v4.url)
_req_v4_emb = _oi_inst.build_request(_ch_v4, "sk-x", "text-emb", "embeddings", {"input": "hi"})
check("v4 embeddings 路径", _req_v4_emb.url == "https://api.example.com/v4/embeddings", _req_v4_emb.url)
_req_v4_img = _oi_inst.build_request(_ch_v4, "sk-x", "dall-e", "images", {"prompt": "cat"})
check("v4 images 路径", _req_v4_img.url == "https://api.example.com/v4/images/generations", _req_v4_img.url)

# base_url 以 /v4 结尾 + api_version=v4 -> 去重后正确拼接
_ch_v4_base = type("MockCh", (), {"base_url": "https://api.example.com/v4", "adapter": "openai_compat",
                                   "api_key": "sk-x", "real_model": lambda self, m: m,
                                   "api_version": "v4"})()
_req_v4_base = _oi_inst.build_request(_ch_v4_base, "sk-x", "gpt-4", "chat", {"messages": []})
check("base_url /v4 + api_version=v4 不重复", _req_v4_base.url == "https://api.example.com/v4/chat/completions", _req_v4_base.url)

# api_version 默认值(v1) 不影响现有渠道
_ch_default = type("MockCh", (), {"base_url": "https://api.openai.com", "adapter": "openai_compat",
                                  "api_key": "sk-x", "real_model": lambda self, m: m})()
_req_default = _oi_inst.build_request(_ch_default, "sk-x", "gpt-4", "chat", {"messages": []})
check("默认 api_version=v1", _req_default.url == "https://api.openai.com/v1/chat/completions", _req_default.url)

print(f"\n结果: {PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
