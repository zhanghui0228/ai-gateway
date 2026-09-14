"""适配器注册表"""
from .anthropic import AnthropicAdapter
from .azure import AzureAdapter
from .base import BaseAdapter
from .gemini import GeminiAdapter
from .openai_compat import OpenAICompatAdapter, USAGE_SENTINEL

ADAPTERS = {
    "openai_compat": OpenAICompatAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
    "azure": AzureAdapter(),
}

# 适配器的友好显示名与描述(供前端展示)
ADAPTER_INFO = {
    "openai_compat": {
        "label": "OpenAI Chat Completions",
        "description": "兼容 OpenAI API 格式的厂商(OpenAI/DeepSeek/Kimi/通义/智谱/OpenRouter/Groq/硅基流动/Ollama 等)",
    },
    "anthropic": {
        "label": "Anthropic Messages API",
        "description": "Claude 原生 Messages 协议，支持 tool_use 和 prompt caching",
    },
    "gemini": {
        "label": "Google Gemini API",
        "description": "Google Gemini generateContent 协议，支持 chat + embeddings",
    },
    "azure": {
        "label": "Azure OpenAI",
        "description": "Azure OpenAI 部署模型 API，使用 api-key 鉴权和部署名路径",
    },
}


def get_adapter(name):
    return ADAPTERS.get(name) or ADAPTERS["openai_compat"]


def adapter_list():
    """返回适配器列表(供前端动态渲染)"""
    return [
        {"name": name, "label": info["label"], "description": info["description"]}
        for name, info in ADAPTER_INFO.items()
        if name in ADAPTERS
    ]


def adapter_label(name):
    """获取适配器的友好显示名"""
    return ADAPTER_INFO.get(name, {}).get("label", name)
