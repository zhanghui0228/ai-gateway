"""内置预设厂商库:base_url / 适配器 / 常用模型 / 参考单价(元/百万token)

价格仅为参考,请以厂商官网为准,控制台可改。
"""

PRESETS = {
    "openai": {
        "name": "OpenAI", "adapter": "openai_compat",
        "base_url": "https://api.openai.com",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3", "o4-mini",
                   "text-embedding-3-large", "dall-e-3"],
        "prices": {"gpt-4o": (17.5, 70.0), "gpt-4o-mini": (1.05, 4.2),
                   "gpt-4.1": (17.5, 70.0), "o3": (70.0, 280.0),
                   "o4-mini": (8.75, 35.0), "text-embedding-3-large": (0.55, 0.0),
                   "dall-e-3": (0.0, 0.0)},
        "needs_proxy": True,
    },
    "anthropic": {
        "name": "Anthropic Claude", "adapter": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-3-7-sonnet-latest", "claude-3-5-haiku-latest"],
        "prices": {"claude-sonnet-4-5": (21.0, 105.0), "claude-opus-4-1": (105.0, 525.0),
                   "claude-3-7-sonnet-latest": (21.0, 105.0), "claude-3-5-haiku-latest": (5.25, 26.25)},
        "needs_proxy": True,
    },
    "gemini": {
        "name": "Google Gemini", "adapter": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "text-embedding-004"],
        "prices": {"gemini-2.5-pro": (9.19, 36.75), "gemini-2.5-flash": (0.7, 2.8),
                   "gemini-2.0-flash": (0.7, 2.1), "text-embedding-004": (0.49, 0.0)},
        "needs_proxy": True,
    },
    "azure": {
        "name": "Azure OpenAI", "adapter": "azure",
        "base_url": "https://YOUR-RESOURCE.openai.azure.com",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "prices": {"gpt-4o": (17.5, 70.0), "gpt-4o-mini": (1.05, 4.2)},
        "needs_proxy": True,
    },
    "deepseek": {
        "name": "DeepSeek", "adapter": "openai_compat",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "prices": {"deepseek-chat": (2.0, 8.0), "deepseek-reasoner": (4.0, 16.0)},
    },
    "moonshot": {
        "name": "Moonshot Kimi", "adapter": "openai_compat",
        "base_url": "https://api.moonshot.cn",
        "models": ["kimi-k2-0905-preview", "moonshot-v1-8k", "moonshot-v1-32k"],
        "prices": {"kimi-k2-0905-preview": (4.0, 16.0), "moonshot-v1-8k": (8.4, 8.4),
                   "moonshot-v1-32k": (16.8, 16.8)},
    },
    "qwen": {
        "name": "阿里通义千问", "adapter": "openai_compat",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo", "text-embedding-v4"],
        "prices": {"qwen-max": (17.2, 68.4), "qwen-plus": (2.84, 11.36), "qwen-turbo": (1.68, 2.74)},
    },
    "zhipu": {
        "name": "智谱 GLM", "adapter": "openai_compat",
        "base_url": "https://open.bigmodel.cn/api/paas",
        "models": ["glm-4.7", "glm-4.6", "glm-4.5-air", "glm-4-flash"],
        "prices": {"glm-4.7": (2.3, 9.2), "glm-4.6": (2.3, 9.2), "glm-4.5-air": (0.5, 2.0),
                   "glm-4-flash": (0.1, 0.1)},
    },
    "openrouter": {
        "name": "OpenRouter", "adapter": "openai_compat",
        "base_url": "https://openrouter.ai/api",
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4.5", "google/gemini-2.5-pro",
                   "deepseek/deepseek-chat"],
        "prices": {},
        "needs_proxy": True,
    },
    "groq": {
        "name": "Groq", "adapter": "openai_compat",
        "base_url": "https://api.groq.com/openai",
        "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "prices": {"llama-3.3-70b-versatile": (4.4, 4.4), "llama-3.1-8b-instant": (0.35, 0.35)},
        "needs_proxy": True,
    },
    "siliconflow": {
        "name": "SiliconFlow 硅基流动", "adapter": "openai_compat",
        "base_url": "https://api.siliconflow.cn",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct",
                   "BAAI/bge-m3"],
        "prices": {"deepseek-ai/DeepSeek-V3": (2.0, 8.0), "Qwen/Qwen2.5-72B-Instruct": (4.13, 4.13)},
    },
    "agentrouter": {
        "name": "AgentRouter 公益站", "adapter": "openai_compat",
        "base_url": "https://ps.air-outer.com",
        "models": ["gpt-5.6-sol", "glm-5.3", "deepseek-v4-flash"],
        "prices": {"gpt-5.6-sol": (27.14, 135.71), "glm-5.3": (2.5, 12.5),
                   "deepseek-v4-flash": (1.0, 3.0)},
        "note": "公益站,Claude/GPT 限量供应(每日 07:00/19:00 两批);国内直连可用",
    },
    "agentrouter_claude": {
        "name": "AgentRouter Claude(Anthropic 协议)", "adapter": "anthropic",
        "base_url": "https://ps.agentrouter.org",
        "models": ["claude-opus-5", "claude-opus-4-8"],
        "prices": {"claude-opus-5": (13.57, 67.85), "claude-opus-4-8": (27.14, 135.71)},
        "note": "Claude 模型走 Anthropic 协议端点;Claude/GPT 限量供应",
    },
    "ollama": {
        "name": "Ollama 本地", "adapter": "openai_compat",
        "base_url": "http://127.0.0.1:11434",
        "models": ["llama3.1", "qwen2.5"],
        "prices": {},
        "local": True,
    },
    "custom": {
        "name": "自定义 (OpenAI 兼容)", "adapter": "openai_compat",
        "base_url": "", "models": [], "prices": {},
    },
}


def preset_list():
    return [
        {"id": pid, "name": p["name"], "adapter": p["adapter"], "base_url": p["base_url"],
         "models": p["models"], "prices": p.get("prices", {}),
         "needs_proxy": p.get("needs_proxy", False), "local": p.get("local", False)}
        for pid, p in PRESETS.items()
    ]
