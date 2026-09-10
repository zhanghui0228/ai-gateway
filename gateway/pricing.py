"""单价计费:渠道覆盖价优先,其次全局价,未配置按 0 计"""
from .db import db
from .models import ModelPrice

_price_cache = None  # {model: (in, out)}


def refresh_cache():
    global _price_cache
    _price_cache = {p.model: (p.input_price, p.output_price)
                    for p in ModelPrice.query.all()}


def get_price(model, channel=None):
    """返回 (input_price, output_price) 元/百万token"""
    if _price_cache is None:
        refresh_cache()
    if channel is not None:
        override = (channel.pricing_override or {}).get(model)
        if override:
            return float(override.get("input", 0) or 0), float(override.get("output", 0) or 0)
    return _price_cache.get(model, (0.0, 0.0))


def calc_cost(model, prompt_tokens, completion_tokens, channel=None):
    pi, po = get_price(model, channel)
    return prompt_tokens / 1e6 * pi + completion_tokens / 1e6 * po


def estimate_tokens(text):
    """拿不到 usage 时的粗略估算:英文~4字符/token,中文~1.6字符/token,取中"""
    if not text:
        return 0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - cjk
    return int(cjk / 1.6 + other / 4)


def upsert_price(model, input_price=None, output_price=None, currency=None,
                 context_window=None, max_output=None, only_missing=False):
    """写入/更新单价与元数据。
    only_missing=True 时为"填充模式":仅补齐缺失项,不覆盖已配置的价格/元数据,
    用于自动获取模型、导入预设价等场景,避免覆盖用户手动配置。"""
    p = db.session.get(ModelPrice, model)
    if p:
        if only_missing:
            if not p.input_price and input_price:
                p.input_price = input_price
            if not p.output_price and output_price:
                p.output_price = output_price
            if p.context_window is None and context_window is not None:
                p.context_window = context_window
            if p.max_output is None and max_output is not None:
                p.max_output = max_output
            if currency and p.currency in (None, "CNY") and currency != "CNY":
                p.currency = currency
        else:
            p.input_price = input_price if input_price is not None else p.input_price
            p.output_price = output_price if output_price is not None else p.output_price
            if currency:
                p.currency = currency
            if context_window is not None:
                p.context_window = context_window
            if max_output is not None:
                p.max_output = max_output
    else:
        db.session.add(ModelPrice(model=model,
                                  input_price=input_price or 0,
                                  output_price=output_price or 0,
                                  currency=currency or "CNY",
                                  context_window=context_window,
                                  max_output=max_output))
    db.session.commit()
    refresh_cache()
