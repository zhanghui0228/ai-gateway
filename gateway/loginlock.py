"""登录失败锁定:防暴力破解"""
import threading
import time

_lock = threading.Lock()
# ip -> {"attempts": int, "locked_until": float}
_attempts: dict[str, dict] = {}


def _cleanup(now):
    """清理已过期的锁定记录"""
    expired = [ip for ip, d in _attempts.items()
               if d.get("locked_until", 0) < now and d["attempts"] == 0]
    for ip in expired:
        del _attempts[ip]


def check_locked(ip: str, max_attempts: int, lockout_duration: int) -> tuple[bool, int]:
    """检查 IP 是否被锁定。返回 (是否锁定, 剩余锁定秒数)。"""
    now = time.time()
    with _lock:
        _cleanup(now)
        record = _attempts.get(ip)
        if not record:
            return False, 0
        locked_until = record.get("locked_until", 0)
        if locked_until > now:
            return True, int(locked_until - now) + 1
        return False, 0


def record_failure(ip: str, max_attempts: int, lockout_duration: int) -> tuple[bool, int]:
    """记录一次登录失败。返回 (是否触发锁定, 剩余锁定秒数)。"""
    now = time.time()
    with _lock:
        _cleanup(now)
        record = _attempts.setdefault(ip, {"attempts": 0, "locked_until": 0})
        # 如果之前有锁定且已过期,重置计数
        if record["locked_until"] and record["locked_until"] <= now:
            record["attempts"] = 0
            record["locked_until"] = 0
        record["attempts"] += 1
        if record["attempts"] >= max_attempts:
            record["locked_until"] = now + lockout_duration
            return True, lockout_duration
        return False, 0


def record_success(ip: str):
    """登录成功,清除失败记录"""
    with _lock:
        _attempts.pop(ip, None)


def reset(ip: str = None):
    """重置登录失败记录。ip=None 时重置全部。"""
    with _lock:
        if ip:
            _attempts.pop(ip, None)
        else:
            _attempts.clear()
