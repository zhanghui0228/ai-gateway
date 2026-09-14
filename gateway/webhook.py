"""Webhook 通知:channel 故障/配额预警/用量告警"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# 通知冷却:事件类型 -> 上次通知时间
_last_notify: dict[str, float] = {}
# 已触发预警的 Key(避免重复告警) -> 已告警级别
_alerted_keys: dict[int, str] = {}

# 通知冷却时间(秒)
COOLDOWN = 300


def _should_notify(event_type: str) -> bool:
    """检查是否超过冷却时间"""
    now = time.time()
    with _lock:
        last = _last_notify.get(event_type, 0)
        if now - last < COOLDOWN:
            return False
        _last_notify[event_type] = now
        return True


def _send_webhook(url: str, payload: dict, timeout: int = 10):
    """发送 webhook 请求(异步)"""
    def _do():
        try:
            resp = httpx.post(url, json=payload, timeout=timeout,
                              headers={"Content-Type": "application/json"})
            if resp.status_code >= 400:
                logger.warning("Webhook 通知失败: %s -> %s", url, resp.status_code)
        except Exception as e:
            logger.warning("Webhook 通知异常: %s", e)

    threading.Thread(target=_do, daemon=True, name="webhook-notify").start()


def get_settings() -> dict:
    """获取 webhook 配置"""
    from .models import Setting
    return {
        "enabled": Setting.get("webhook_enabled", "0") == "1",
        "url": Setting.get("webhook_url", ""),
        "on_channel_fail": Setting.get("webhook_on_channel_fail", "1") == "1",
        "on_quota_alert": Setting.get("webhook_on_quota_alert", "1") == "1",
    }


def notify_channel_fail(channel_name: str, error: str):
    """渠道故障通知"""
    settings = get_settings()
    if not settings["enabled"] or not settings["url"] or not settings["on_channel_fail"]:
        return
    event_type = f"channel_fail:{channel_name}"
    if not _should_notify(event_type):
        return

    payload = {
        "event": "channel_fail",
        "time": datetime.now(timezone.utc).isoformat(),
        "channel": channel_name,
        "error": error[:500],
    }
    _send_webhook(settings["url"], payload)


def notify_quota_alert(api_key_name: str, used: int, total: int, level: str):
    """配额用量预警(level: warning/critical)"""
    settings = get_settings()
    if not settings["enabled"] or not settings["url"] or not settings["on_quota_alert"]:
        return
    event_type = f"quota_alert:{api_key_name}"
    if not _should_notify(event_type):
        return

    payload = {
        "event": "quota_alert",
        "time": datetime.now(timezone.utc).isoformat(),
        "key_name": api_key_name,
        "used_tokens": used,
        "total_tokens": total,
        "usage_percent": round(used / total * 100, 1) if total > 0 else 0,
        "level": level,
    }
    _send_webhook(settings["url"], payload)


def check_quota_alert(api_key_row):
    """检查配额用量,达到阈值时发送预警。
    阈值:80% warning, 95% critical。
    使用 _alerted_keys 避免重复告警。"""
    if api_key_row.quota_tokens is None or api_key_row.quota_tokens <= 0:
        return
    used = api_key_row.used_tokens or 0
    total = api_key_row.quota_tokens
    if total <= 0:
        return

    pct = used / total
    if pct >= 0.95:
        level = "critical"
    elif pct >= 0.80:
        level = "warning"
    else:
        # 用量回落,清除告警状态
        with _lock:
            _alerted_keys.pop(api_key_row.id, None)
        return

    with _lock:
        # 已发送过同级别或更高级别告警,跳过
        prev = _alerted_keys.get(api_key_row.id)
        if prev == "critical" or (prev == "warning" and level == "warning"):
            return
        _alerted_keys[api_key_row.id] = level

    notify_quota_alert(
        api_key_name=api_key_row.name or api_key_row.key[:8],
        used=used,
        total=total,
        level=level)


def reset_alerts():
    """清除所有预警状态"""
    with _lock:
        _alerted_keys.clear()
        _last_notify.clear()
