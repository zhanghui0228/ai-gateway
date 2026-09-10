"""负载均衡 + 熔断器

选路策略:
1. 过滤出 启用 + 支持该模型 + 未被熔断 的渠道
2. 按优先级分组,高优先级组优先;组内按权重加权随机
3. 请求失败故障转移到下一个候选(跨组降级),已尝试的渠道不重复
熔断:
- 渠道连续失败 N 次 -> 打开熔断(BREAKER_COOLDOWN 秒)
- 冷却结束后进入半开:放行一次真实请求探活,成功则关闭熔断,失败则重新熔断
"""
import random
import threading
from datetime import datetime, timedelta, timezone

from .db import db
from .models import Channel, Setting


class CircuitState:
    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


_lock = threading.Lock()
# 渠道内存状态:{channel_id: {"state":..., "half_open_used": bool}}
_state = {}


def _now():
    return datetime.now(timezone.utc)


def breaker_info(channel):
    """返回 (state, 剩余冷却秒)"""
    if not channel.breaker_until:
        return CircuitState.CLOSED, 0
    try:
        until = datetime.fromisoformat(channel.breaker_until)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return CircuitState.CLOSED, 0
    if _now() >= until:
        return CircuitState.HALF_OPEN, 0
    return CircuitState.OPEN, (until - _now()).total_seconds()


def _db_update(channel, **kw):
    for k, v in kw.items():
        setattr(channel, k, v)
    db.session.commit()


def record_success(channel):
    with _lock:
        _state.pop(channel.id, None)
    if channel.fail_streak or channel.breaker_until:
        _db_update(channel, fail_streak=0, breaker_until="")


def record_failure(channel, threshold, cooldown):
    channel.fail_streak = (channel.fail_streak or 0) + 1
    if channel.fail_streak >= threshold:
        until = (_now() + timedelta(seconds=cooldown)).isoformat()
        _db_update(channel, fail_streak=channel.fail_streak, breaker_until=until)
        with _lock:
            _state.pop(channel.id, None)
    else:
        _db_update(channel, fail_streak=channel.fail_streak)


def try_acquire_half_open(channel):
    """半开状态下只放行一次真实请求(探活)"""
    with _lock:
        st = _state.get(channel.id)
        if st and st.get("state") == CircuitState.HALF_OPEN and st.get("half_open_used"):
            return False
        _state[channel.id] = {"state": CircuitState.HALF_OPEN, "half_open_used": True}
    return True


def release_half_open(channel, success):
    if not success:
        # 探活失败,重新熔断(冷却翻倍,最多30分钟)
        cooldown = min(60 * 30, 60 * (2 ** min(5, (channel.fail_streak or 1) // 5 or 1)))
        until = (_now() + timedelta(seconds=cooldown)).isoformat()
        _db_update(channel, breaker_until=until)
    with _lock:
        _state.pop(channel.id, None)


def _breaker_params():
    try:
        threshold = int(Setting.get("breaker_threshold", 5))
        cooldown = int(Setting.get("breaker_cooldown", 60))
    except (TypeError, ValueError):
        threshold, cooldown = 5, 60
    return threshold, cooldown


def pick_candidates(model, exclude_ids):
    """按优先级分层 + 层内加权随机,返回候选渠道有序列表"""
    channels = Channel.query.filter_by(enabled=True).all()
    usable = []
    for ch in channels:
        if ch.id in exclude_ids:
            continue
        models = ch.models or []
        if models and model not in models:
            continue
        state, _ = breaker_info(ch)
        if state == CircuitState.OPEN:
            continue
        if state == CircuitState.HALF_OPEN and not try_acquire_half_open(ch):
            continue
        usable.append(ch)

    # 分层:priority 降序
    ordered = []
    groups = {}
    for ch in usable:
        groups.setdefault(ch.priority or 0, []).append(ch)
    for pri in sorted(groups.keys(), reverse=True):
        pool = groups[pri]
        # 组内加权随机抽取
        while pool:
            weights = [max(1, c.weight or 1) for c in pool]
            picked = random.choices(pool, weights=weights, k=1)[0]
            ordered.append(picked)
            pool.remove(picked)
    return ordered


def note_failure(channel):
    threshold, cooldown = _breaker_params()
    # 半开探活失败走 release 分支
    state, _ = breaker_info(channel)
    if state == CircuitState.HALF_OPEN:
        with _lock:
            st = _state.get(channel.id)
        if st and st.get("half_open_used"):
            release_half_open(channel, success=False)
            return
    record_failure(channel, threshold, cooldown)


def note_success(channel):
    record_success(channel)
