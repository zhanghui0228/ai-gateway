"""适配器基类:统一 OpenAI 格式 <-> 上游原生协议互转

职责:
- build_request:   把 OpenAI 请求体转成上游请求(url/headers/body)
- adapt_response:  把上游非流式响应转回 OpenAI 格式
- transform_stream: 把上游 SSE 流转成 OpenAI SSE 流,同时捕获 usage
- adapt_usage:     从上游响应提取 usage
"""


class AdapterError(Exception):
    """适配器不支持的能力(如 Anthropic 不支持图片生成)"""


class UpstreamRequest:
    def __init__(self, url, headers, json_body, stream=False):
        self.url = url
        self.headers = headers
        self.json_body = json_body
        self.stream = stream


class BaseAdapter:
    name = "base"
    KIND_CHAT = "chat"
    KIND_COMPLETIONS = "completions"
    KIND_EMBEDDINGS = "embeddings"
    KIND_IMAGES = "images"

    def build_request(self, channel, api_key, model, kind, openai_body):
        raise NotImplementedError

    def adapt_response(self, upstream_json, kind):
        """非流式:upstream json -> openai json"""
        return upstream_json

    def transform_stream(self, sse_lines):
        """流式:上游 SSE data 行迭代器 -> 生成 OpenAI 格式 SSE data 行(str)
        各适配器需自行捕获 usage 并在流结束后以 ('__usage__', usage_dict) 形式 yield"""
        for line in sse_lines:
            yield line

    @staticmethod
    def extract_usage(upstream_json):
        u = (upstream_json or {}).get("usage") or {}
        pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        return {"prompt_tokens": pt, "completion_tokens": ct}

    @staticmethod
    def extract_token_details(upstream_json):
        """从非流式响应提取缓存命中信息,返回 {"cache_read": n, "cache_creation": n}"""
        u = (upstream_json or {}).get("usage") or {}
        d = u.get("prompt_tokens_details") or {}
        return {"cache_read": int(d.get("cached_tokens") or 0), "cache_creation": 0}

    def models_request(self, channel, api_key):
        """模型列表端点(免费,不消耗 token):用于定时健康探测与自动获取模型。
        返回 (url, headers)"""
        base = (channel.base_url or "").rstrip("/")
        headers = {}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        return base + "/v1/models", headers

    @staticmethod
    def parse_models(data):
        """解析模型列表响应,返回模型名列表"""
        return [m.get("id") for m in (data.get("data") or []) if m.get("id")]

    def models_fallback_request(self, channel):
        """备用模型清单来源(上游禁用 /v1/models 时使用)。
        返回 [(url, headers)];默认无。"""
        return []

    @staticmethod
    def parse_fallback_models(data):
        """解析备用来源的模型清单,返回模型名列表"""
        return []

    @staticmethod
    def parse_models_meta(data):
        """解析模型列表响应中自带的上游元数据(OpenRouter 风格)。
        返回 {model_id: {"context":..., "max_output":..., "input_price":..., "output_price":..., "currency":...}}"""
        out = {}
        for m in (data.get("data") or []):
            mid = m.get("id")
            if not mid:
                continue
            meta = {}
            if m.get("context_length"):
                try:
                    meta["context"] = int(m["context_length"])
                except (TypeError, ValueError):
                    pass
            pr = m.get("pricing") or {}
            if pr.get("prompt") is not None:
                try:
                    # OpenRouter 价格单位为 美元/token,换算为 美元/百万token
                    meta["input_price"] = round(float(pr["prompt"]) * 1e6, 4)
                    meta["output_price"] = round(float(pr.get("completion") or 0) * 1e6, 4)
                    meta["currency"] = "USD"
                except (TypeError, ValueError):
                    pass
            top = m.get("top_provider") or {}
            if top.get("max_completion_tokens"):
                try:
                    meta["max_output"] = int(top["max_completion_tokens"])
                except (TypeError, ValueError):
                    pass
            if meta:
                out[mid] = meta
        return out
