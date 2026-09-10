"""统计聚合查询:控制台报表与大屏共用"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from gateway.db import db
from gateway.models import Channel, UsageLog


def _parse_ts(s):
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _range_or_default(start=None, end=None, days=30):
    now = datetime.now(timezone.utc)
    end_dt = _parse_ts(end) if end else now
    start_dt = _parse_ts(start) if start else end_dt - timedelta(days=days)
    return start_dt, end_dt


def overview(days=1):
    """大屏核心指标"""
    start, now = _range_or_default(days=days)
    q = UsageLog.query.filter(UsageLog.created_at >= start)
    total = q.count()
    success = q.filter_by(success=True).count()
    total_tokens = db.session.query(
        func.coalesce(func.sum(UsageLog.total_tokens), 0)).filter(
        UsageLog.created_at >= start).scalar()
    cost = db.session.query(
        func.coalesce(func.sum(UsageLog.cost), 0)).filter(
        UsageLog.created_at >= start).scalar()
    avg_latency = db.session.query(
        func.coalesce(func.avg(UsageLog.latency_ms), 0)).filter(
        UsageLog.created_at >= start, UsageLog.success.is_(True)).scalar()
    online = Channel.query.filter_by(enabled=True).count()
    channels = Channel.query.count()
    today0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_tokens = db.session.query(
        func.coalesce(func.sum(UsageLog.total_tokens), 0)).filter(
        UsageLog.created_at >= today0).scalar()
    return {
        "total_calls": total, "success_calls": success,
        "success_rate": round(success / total * 100, 1) if total else 100.0,
        "total_tokens": int(total_tokens), "today_tokens": int(today_tokens),
        "cost": round(float(cost), 4), "avg_latency_ms": int(avg_latency),
        "online_channels": online, "total_channels": channels,
    }


def hourly_trend(days=1):
    """24小时逐小时调用量/tokens/费用(北京时间 UTC+8)"""
    start, _ = _range_or_default(days=days)
    # SQLite 存储的是 UTC,用 +8 hours 转为北京时间后再按小时聚合
    bj = func.strftime("%Y-%m-%dT%H:00", UsageLog.created_at, "+8 hours")
    rows = (db.session.query(
                bj,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0))
            .filter(UsageLog.created_at >= start)
            .group_by(bj)
            .order_by(bj).all())
    return [{"hour": r[0], "calls": r[1], "tokens": int(r[2]),
             "cost": round(float(r[3]), 4)} for r in rows]


def _group_by(field, days=30, start=None, end=None):
    start_dt, end_dt = _range_or_default(start, end, days=days)
    rows = (db.session.query(field, func.count(UsageLog.id),
                             func.coalesce(func.sum(UsageLog.total_tokens), 0),
                             func.coalesce(func.sum(UsageLog.cost), 0),
                             func.coalesce(func.avg(UsageLog.latency_ms), 0),
                             func.sum(db.case((UsageLog.success.is_(True), 1), else_=0)))
            .filter(UsageLog.created_at >= start_dt)
            .group_by(field).all())
    return [{"name": r[0] or "未知", "calls": r[1], "tokens": int(r[2]),
             "cost": round(float(r[3]), 4), "avg_latency_ms": int(r[4]),
             "success_rate": round(r[5] / r[1] * 100, 1) if r[1] else 100.0}
            for r in rows]


def by_model(days=30, start=None, end=None):
    return _group_by(UsageLog.model, days, start, end)


def by_channel(days=30, start=None, end=None):
    return _group_by(UsageLog.channel_name, days, start, end)


def by_key(days=30, start=None, end=None):
    return _group_by(UsageLog.key_name, days, start, end)


def recent_logs(limit=50, only_success=None):
    q = UsageLog.query.order_by(UsageLog.id.desc())
    if only_success is True:
        q = q.filter_by(success=True)
    elif only_success is False:
        q = q.filter_by(success=False)
    return [l.to_dict() for l in q.limit(min(limit, 500)).all()]


def daily_usage(days=14):
    """按天统计:每天 tokens(输入/输出/缓存)、调用数、成功数、费用、活跃模型数"""
    start, _ = _range_or_default(days=days)
    rows = (db.session.query(
                func.date(UsageLog.created_at),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.cache_read_tokens), 0),
                func.count(UsageLog.id),
                func.sum(db.case((UsageLog.success.is_(True), 1), else_=0)),
                func.coalesce(func.sum(UsageLog.cost), 0),
                func.count(func.distinct(UsageLog.model)))
            .filter(UsageLog.created_at >= start)
            .group_by(func.date(UsageLog.created_at))
            .order_by(func.date(UsageLog.created_at)).all())
    return [{"date": r[0], "total_tokens": int(r[1]), "prompt_tokens": int(r[2]),
             "completion_tokens": int(r[3]), "cache_tokens": int(r[4]),
             "calls": r[5], "success": int(r[6] or 0), "cost": round(float(r[7]), 4),
             "models": int(r[8])} for r in rows]


def daily_model_calls(days=14):
    """按天 x 模型的调用次数矩阵(每日模型调用展示)"""
    start, _ = _range_or_default(days=days)
    rows = (db.session.query(func.date(UsageLog.created_at), UsageLog.model,
                             func.count(UsageLog.id))
            .filter(UsageLog.created_at >= start)
            .group_by(func.date(UsageLog.created_at), UsageLog.model).all())
    counts = {}
    for d, m, c in rows:
        counts.setdefault(d, {})[m] = c
    return counts


def hot_models(days=7, limit=10):
    """热点模型排名(按调用量)"""
    rows = _group_by(UsageLog.model, days=days)
    rows.sort(key=lambda x: x["calls"], reverse=True)
    return rows[:limit]


def hourly_heatmap(days=7):
    """调用时段热点:7x24 矩阵 [weekday][hour] = calls(北京时间 UTC+8)
    weekday: 0=周一 ... 6=周日"""
    start, _ = _range_or_default(days=days)
    # +8 hours 转为北京时间后再聚合星期几与小时
    bj_weekday = func.strftime("%w", UsageLog.created_at, "+8 hours")
    bj_hour = func.strftime("%H", UsageLog.created_at, "+8 hours")
    rows = (db.session.query(bj_weekday, bj_hour, func.count(UsageLog.id))
            .filter(UsageLog.created_at >= start)
            .group_by(bj_weekday, bj_hour).all())
    matrix = [[0] * 24 for _ in range(7)]
    for w, h, cnt in rows:
        idx = (int(w) - 1) % 7    # sqlite 周日=0 -> 转为 周一=0
        matrix[idx][int(h)] = cnt
    return matrix


def model_hour_heatmap(days=7, limit_models=8):
    """模型 x 24小时 调用热点(哪些模型在什么时段被调用, 北京时间 UTC+8)"""
    start, _ = _range_or_default(days=days)
    bj_hour = func.strftime("%H", UsageLog.created_at, "+8 hours")
    rows = (db.session.query(UsageLog.model, bj_hour, func.count(UsageLog.id))
            .filter(UsageLog.created_at >= start)
            .group_by(UsageLog.model, bj_hour).all())
    counts = {}
    for m, h, cnt in rows:
        counts.setdefault(m, {})[int(h)] = cnt
    top = sorted(counts.items(), key=lambda kv: sum(kv[1].values()), reverse=True)[:limit_models]
    data = []
    for mi, (m, hours) in enumerate(top):
        for h, cnt in hours.items():
            data.append([h, mi, cnt])
    return {"models": [m for m, _ in top], "data": data}
