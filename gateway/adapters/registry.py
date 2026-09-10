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


def get_adapter(name):
    return ADAPTERS.get(name) or ADAPTERS["openai_compat"]
