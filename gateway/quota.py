"""额度预检与扣减、用量统计落库"""
import threading

from . import events, pricing
from .db import db
from .models import Setting, UsageLog

_lock = threading.Lock()


def check_quota(api_key):
    """返回 (ok, 剩余额度)。quota=-1 视为无限"""
    if api_key.quota_tokens is None or api_key.quota_tokens < 0:
        return True, -1
    remaining = api_key.quota_tokens - (api_key.used_tokens or 0)
    return remaining > 0, remaining


def consume(api_key, total_tokens):
    if total_tokens <= 0:
        return
    with _lock:
        api_key.used_tokens = (api_key.used_tokens or 0) + total_tokens
        db.session.commit()


def log_usage(**kw):
    """写入调用明细并推送大屏事件"""
    entry = UsageLog(**kw)
    db.session.add(entry)
    db.session.commit()
    events.publish("usage", entry.to_dict())
    return entry


def get_setting_int(key, default):
    try:
        return int(Setting.get(key, default))
    except (TypeError, ValueError):
        return default


def calc_and_get_usage(model, channel, usage, request_text, response_text):
    """解析 usage;拿不到则估算。返回 (prompt_tokens, completion_tokens, estimated)"""
    if usage:
        pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if pt or ct:
            return pt, ct, False
    pt = pricing.estimate_tokens(request_text)
    ct = pricing.estimate_tokens(response_text)
    return pt, ct, True
