"""Azure OpenAI 适配器:部署名映射 + api-version 查询参数"""
from .base import BaseAdapter, UpstreamRequest


class AzureAdapter(BaseAdapter):
    name = "azure"

    def build_request(self, channel, api_key, model, kind, openai_body):
        base = (channel.base_url or "").rstrip("/")
        version = channel.azure_api_version or "2024-10-21"
        body = dict(openai_body)
        body.pop("model", None)  # 部署名已在 URL,无需 body model
        stream = bool(body.get("stream"))
        if stream and kind == self.KIND_CHAT and "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}

        if kind == self.KIND_CHAT:
            path = f"/openai/deployments/{model}/chat/completions"
        elif kind == self.KIND_COMPLETIONS:
            path = f"/openai/deployments/{model}/completions"
        elif kind == self.KIND_EMBEDDINGS:
            path = f"/openai/deployments/{model}/embeddings"
            body.pop("stream", None)
            stream = False
        elif kind == self.KIND_IMAGES:
            path = f"/openai/images/generations"
            body.pop("stream", None)
            stream = False
        else:
            path = f"/openai/deployments/{model}/chat/completions"

        headers = {"Content-Type": "application/json", "api-key": api_key}
        return UpstreamRequest(f"{base}{path}?api-version={version}", headers, body, stream)

    def models_request(self, channel, api_key):
        base = (channel.base_url or "").rstrip("/")
        version = channel.azure_api_version or "2024-10-21"
        headers = {"api-key": api_key} if api_key else {}
        return f"{base}/openai/deployments?api-version={version}", headers
