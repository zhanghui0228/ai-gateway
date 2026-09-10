"""模型元数据知识库:上下文窗口 / 最大输出 / 参考单价

数据来源:上游模型列表端点(如 OpenRouter 返回 context_length/pricing)优先,
未提供时按本库匹配(模型名小写前缀匹配,长前缀优先)。
价格为元/百万 token,仅供参考,控制台可改。
"""

# (前缀, 上下文窗口, 最大输出, 输入价, 输出价)  价格单位: 元/百万token
_META = [
    # OpenAI
    ("gpt-4o-mini", 128000, 16384, 1.05, 4.2),
    ("gpt-4o", 128000, 16384, 17.5, 70.0),
    ("gpt-4.1-mini", 1047576, 32768, 4.2, 16.8),
    ("gpt-4.1-nano", 1047576, 32768, 0.84, 3.36),
    ("gpt-4.1", 1047576, 32768, 17.5, 70.0),
    ("gpt-4-turbo", 128000, 4096, 70.0, 210.0),
    ("o4-mini", 200000, 100000, 8.75, 35.0),
    ("o3-mini", 200000, 100000, 8.75, 35.0),
    ("o3", 200000, 100000, 70.0, 280.0),
    ("text-embedding-3-large", 8191, 0, 0.55, 0.0),
    ("text-embedding-3-small", 8191, 0, 0.11, 0.0),
    ("dall-e-3", 0, 0, 0.0, 0.0),
    # Anthropic
    ("claude-opus-4", 200000, 32000, 105.0, 525.0),
    ("claude-sonnet-4", 200000, 64000, 21.0, 105.0),
    ("claude-3-7-sonnet", 200000, 64000, 21.0, 105.0),
    ("claude-3-5-sonnet", 200000, 8192, 21.0, 105.0),
    ("claude-3-5-haiku", 200000, 8192, 5.25, 26.25),
    # Gemini
    ("gemini-2.5-pro", 1048576, 65536, 9.19, 36.75),
    ("gemini-2.5-flash", 1048576, 65536, 0.7, 2.8),
    ("gemini-2.0-flash", 1048576, 8192, 0.7, 2.1),
    ("gemini-1.5-pro", 2097152, 8192, 12.5, 50.0),
    ("gemini-1.5-flash", 1048576, 8192, 0.7, 2.1),
    ("text-embedding-004", 2048, 0, 0.49, 0.0),
    # DeepSeek
    ("deepseek-reasoner", 65536, 65536, 4.0, 16.0),
    ("deepseek-chat", 65536, 8192, 2.0, 8.0),
    # Kimi
    ("kimi-k2", 262144, 16384, 4.0, 16.0),
    ("moonshot-v1-128k", 131072, 16384, 33.6, 33.6),
    ("moonshot-v1-32k", 32768, 16384, 16.8, 16.8),
    ("moonshot-v1-8k", 8192, 8192, 8.4, 8.4),
    # 通义
    ("qwen-max", 32768, 8192, 17.2, 68.4),
    ("qwen-plus", 131072, 8192, 2.84, 11.36),
    ("qwen-turbo", 1000000, 8192, 1.68, 2.74),
    # 智谱
    ("glm-4.5-air", 131072, 98304, 0.5, 2.0),
    ("glm-4.5-flash", 131072, 98304, 0.0, 0.0),
    ("glm-4.5", 131072, 98304, 2.3, 9.2),
    ("glm-4.6", 131072, 98304, 2.3, 9.2),
    ("glm-4.7", 131072, 98304, 2.3, 9.2),
    ("glm-4-flash", 131072, 98304, 0.1, 0.1),
    # 嵌入
    ("bge-m3", 8192, 0, 0.07, 0.0),
    ("text-embedding-v4", 8192, 0, 0.5, 0.0),
]

# 长前缀优先,避免 "gpt-4o" 抢先匹配 "gpt-4o-mini"
_ORDERED = sorted(_META, key=lambda x: -len(x[0]))


def lookup(model):
    """按模型名匹配元数据,返回 dict 或 None"""
    if not model:
        return None
    name = model.lower()
    for pat, ctx, max_out, pi, po in _ORDERED:
        if name == pat or name.startswith(pat):
            return {"context": ctx, "max_output": max_out,
                    "input_price": pi, "output_price": po, "currency": "CNY"}
    return None
