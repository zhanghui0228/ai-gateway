"""API 速率限制:按 Key 滑动窗口限流(内存级,单进程适用)"""
import threading
import time
from collections import defaultdict

_lock = threading.Lock()
# key -> [(timestamp, count), ...] 按分钟窗口聚合
_buckets: dict[str, list[tuple[float, int]]] = defaultdict(list)


def _cleanup(now):
    """清理超过 2 分钟的旧窗口"""
    cutoff = now - 120
    for key in list(_buckets.keys()):
        _buckets[key] = [(t, c) for t, c in _buckets[key] if t > cutoff]
        if not _buckets[key]:
            del _buckets[key]


def check_rate(key: str, max_rpm: int, max_rph: int) -> tuple[bool, dict]:
    """检查是否超出速率限制。返回 (allowed, info)。

    Args:
        key: 限流键(通常为 API Key)
        max_rpm: 每分钟最大请求数
        max_rph: 每小时最大请求数

    Returns:
        (是否允许, 限流信息字典)
    """
    now = time.time()
    minute_window = int(now // 60) * 60
    hour_window = int(now // 3600) * 3600

    with _lock:
        _cleanup(now)
        bucket = _buckets[key]

        # 统计当前分钟和小时的请求数
        minute_count = sum(c for t, c in bucket if t >= minute_window)
        hour_count = sum(c for t, c in bucket if t >= hour_window)

        # 记录本次请求
        if bucket and bucket[-1][0] >= minute_window:
            bucket[-1] = (bucket[-1][0], bucket[-1][1] + 1)
        else:
            bucket.append((minute_count > 0 and minute_window or now, 1))

        info = {
            "limit_rpm": max_rpm,
            "limit_rph": max_rph,
            "remaining_rpm": max(0, max_rpm - minute_count - 1),
            "remaining_rph": max(0, max_rph - hour_count - 1),
            "reset_rpm": minute_window + 60,
            "reset_rph": hour_window + 3600,
        }

        if minute_count >= max_rpm:
            info["retry_after"] = int(minute_window + 60 - now) + 1
            return False, info
        if hour_count >= max_rph:
            info["retry_after"] = int(hour_window + 3600 - now) + 1
            return False, info

        return True, info


def reset(key: str = None):
    """重置限流计数。key=None 时重置全部。"""
    with _lock:
        if key:
            _buckets.pop(key, None)
        else:
            _buckets.clear()
