"""自动模型降级: 记录 per-model 失败,在 auto 候选排序中降权,自动恢复

设计:
- 内存级追踪(类似 balancer._state),无需改 DB schema
- 模型连续失败 N 次后进入降级状态,在 auto 候选列表中排到末尾
- 冷却时间随失败次数指数退避: 60s -> 120s -> 240s -> max 600s
- 到期自动恢复,成功调用后立即清除
"""
import threading
import time

_lock = threading.Lock()
# {model_name: {"failures": int, "cooldown_until": float, "last_error": str, "degraded_at": float}}
_state = {}

# 初始冷却 60s,每次翻倍,最多 600s
_BASE_COOLDOWN = 60
_MAX_COOLDOWN = 600


def record_model_failure(model, error=""):
    """记录模型调用失败,增加失败计数并延长冷却"""
    now = time.time()
    with _lock:
        entry = _state.get(model)
        if not entry:
            _state[model] = {
                "failures": 1,
                "cooldown_until": now + _BASE_COOLDOWN,
                "last_error": (error or "")[:200],
                "degraded_at": now,
            }
        else:
            entry["failures"] += 1
            cooldown = min(_MAX_COOLDOWN, _BASE_COOLDOWN * (2 ** (entry["failures"] - 1)))
            entry["cooldown_until"] = now + cooldown
            entry["last_error"] = (error or "")[:200]
            entry["degraded_at"] = now


def record_model_success(model):
    """模型调用成功,清除降级状态"""
    with _lock:
        _state.pop(model, None)


def is_degraded(model):
    """检查模型当前是否处于降级状态(到期自动清理)"""
    with _lock:
        entry = _state.get(model)
        if not entry:
            return False
        if time.time() >= entry["cooldown_until"]:
            _state.pop(model, None)
            return False
        return True


def sort_candidates(candidates):
    """对候选模型排序:正常模型保持原序排前,降级模型排后(按剩余冷却升序)"""
    with _lock:
        now = time.time()
        normal, degraded = [], []
        for m in candidates:
            entry = _state.get(m)
            if entry and time.time() < entry["cooldown_until"]:
                degraded.append((m, entry["cooldown_until"] - now))
            else:
                if entry:
                    _state.pop(m, None)  # 过期清理
                normal.append(m)
        # 降级模型按剩余冷却时间升序(即将恢复的排前面)
        degraded.sort(key=lambda x: x[1])
        return normal + [m for m, _ in degraded]


def get_status():
    """返回当前降级状态列表(供 API/前端展示)"""
    now = time.time()
    result = []
    with _lock:
        for model, entry in list(_state.items()):
            remaining = entry["cooldown_until"] - now
            if remaining <= 0:
                _state.pop(model, None)
                continue
            result.append({
                "model": model,
                "failures": entry["failures"],
                "cooldown_remaining": int(remaining),
                "last_error": entry.get("last_error", ""),
                "degraded_at": entry.get("degraded_at", 0),
            })
    result.sort(key=lambda x: x["cooldown_remaining"], reverse=True)
    return result


def reset(model=None):
    """手动清除降级状态。model=None 清除全部"""
    with _lock:
        if model is None:
            _state.clear()
        else:
            _state.pop(model, None)
