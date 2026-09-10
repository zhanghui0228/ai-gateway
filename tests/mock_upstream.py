"""本地 mock OpenAI 兼容上游,用于联调验证网关转发链路"""
import json
import sys
import time

from flask import Flask, Response, request

app = Flask(__name__)
FAIL = "--fail" in sys.argv  # 模拟上游故障


@app.route("/v1/models", methods=["GET"])
def models():
    if FAIL:
        return json.dumps({"error": {"message": "down"}}), 500
    # 模拟带元数据的模型列表(OpenRouter 风格)
    return json.dumps({"object": "list", "data": [
        {"id": "test-model", "context_length": 131072,
         "pricing": {"prompt": "0.0000021", "completion": "0.0000084"},
         "top_provider": {"max_completion_tokens": 8192}},
        {"id": "test-model-pro", "context_length": 262144,
         "pricing": {"prompt": "0.0000084", "completion": "0.0000336"},
         "top_provider": {"max_completion_tokens": 16384}},
        {"id": "test-embed"},
        {"id": "deepseek-chat"},
    ]}), 200, {"Content-Type": "application/json"}


@app.route("/v1/chat/completions", methods=["POST"])
def chat():
    body = request.get_json(force=True)
    if FAIL:
        return json.dumps({"error": {"message": "mock upstream down"}}), 500
    if body.get("stream"):
        def gen():
            for i, w in enumerate(["你好", ",", "这是", "网关", "流式", "测试"]):
                chunk = {"id": "mock-1", "object": "chat.completion.chunk",
                         "created": int(time.time()), "model": body["model"],
                         "choices": [{"index": 0, "delta": {"content": w}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            final = {"id": "mock-1", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": body["model"],
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18,
                               "prompt_tokens_details": {"cached_tokens": 4}}}
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"
        return Response(gen(), content_type="text/event-stream")
    return json.dumps({
        "id": "mock-1", "object": "chat.completion", "created": int(time.time()),
        "model": body["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "你好,这是网关非流式测试"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20,
                  "prompt_tokens_details": {"cached_tokens": 5}},
    }, ensure_ascii=False), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    from waitress import serve
    print("mock upstream on :9999, fail mode:", FAIL)
    serve(app, host="127.0.0.1", port=9999, threads=8)
