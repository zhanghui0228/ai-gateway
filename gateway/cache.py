"""响应缓存: 内存 LRU + SQLite 双层缓存,减少重复请求的上游 token 消耗"""
import hashlib
import json
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from .db import db
from .models import ResponseCacheEntry, CacheEvent, Setting

# 影响模型输出的字段(用于构造缓存键)
_CACHE_KEY_FIELDS = (
    "model", "messages", "temperature", "top_p", "top_k",
    "max_tokens", "max_completion_tokens",
    "tools", "tool_choice", "response_format",
    "seed", "stop", "n",
    "frequency_penalty", "presence_penalty",
    "logit_bias", "logprobs", "top_logprobs",
    "input",   # embeddings
    "stream",  # 区分流式/非流式,确保缓存键不同
    # include_usage 等:显式关闭 usage 的请求与未设置/开启的请求响应不同,
    # 纳入缓存键可避免向未请求 usage 的客户端回放 usage chunk
    "stream_options",
    "user",    # 各厂商按 user 做风控/统计,纳入键保证条目隔离
)

# 每种 kind 的默认 TTL(秒)
_KIND_TTL = {"chat": 300, "completions": 300, "embeddings": 3600}


def _now():
    return datetime.now(timezone.utc)


def _utc_from_naive(dt):
    """将 SQLite 读取的 naive datetime 统一转为 UTC aware datetime。
    SQLite 存储会丢失 tzinfo,所有写入均为 UTC,读取时按此假设还原。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def should_cache(kind, body):
    """判断请求是否可缓存: 支持的 kind（流式由 cache_stream 设置控制）"""
    if kind not in _KIND_TTL:
        return False
    if bool(body.get("stream")):
        try:
            return Setting.get("cache_stream", "1") != "0"
        except Exception:
            return True
    return True


def build_cache_key(kind, model, body) -> str:
    """构造缓存键: 提取影响输出的字段 → 排序 JSON → SHA256 hex"""
    key_obj = {}
    for f in _CACHE_KEY_FIELDS:
        if f in body:
            key_obj[f] = body[f]
    key_obj["_model"] = model
    key_obj["_kind"] = kind
    blob = json.dumps(key_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _get_ttl(kind):
    """从设置读取 TTL,回退到默认值"""
    try:
        ttl = int(Setting.get("cache_ttl", _KIND_TTL.get(kind, 300)))
        return max(10, ttl)
    except (TypeError, ValueError):
        return _KIND_TTL.get(kind, 300)


def _get_max_memory():
    try:
        return max(10, int(Setting.get("cache_max_memory", 200)))
    except (TypeError, ValueError):
        return 200


def _get_max_sqlite():
    try:
        return max(100, int(Setting.get("cache_max_sqlite", 10000)))
    except (TypeError, ValueError):
        return 10000


def cache_enabled():
    try:
        return Setting.get("cache_enabled", "1") != "0"
    except Exception:
        return True


class _CacheEntry:
    """内存中的缓存条目"""
    __slots__ = ("response_body", "chunks", "prompt_tokens", "completion_tokens",
                 "model", "kind", "expires_at")

    def __init__(self, response_body, chunks, prompt_tokens, completion_tokens,
                 model, kind, expires_at):
        self.response_body = response_body  # 非流式: JSON 字符串
        self.chunks = chunks                # 流式: SSE data 字符串列表
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model = model
        self.kind = kind
        self.expires_at = expires_at

    @property
    def is_stream(self):
        return self.chunks is not None


class ResponseCache:
    """双层缓存: 内存 LRU(热路径) + SQLite(持久化)"""

    def __init__(self):
        self._lru: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        # 每小时命中计数(用于趋势图,进程内)
        self._hourly_hits: dict[str, int] = {}

    # ---------- 核心操作 ----------

    def get(self, cache_key: str):
        """查缓存: 先内存 LRU, 后 SQLite。命中后更新统计。"""
        with self._lock:
            entry = self._lru.get(cache_key)
            if entry:
                if _now() < entry.expires_at:
                    # LRU 移到末尾(最近使用)
                    self._lru.move_to_end(cache_key)
                    self._hits += 1
                    self._note_hit()
                    return entry
                else:
                    # 过期,移除
                    del self._lru[cache_key]

        # 查 SQLite
        row = db.session.get(ResponseCacheEntry, cache_key)
        if row and row.expires_at and _utc_from_naive(row.expires_at) > _now():
            # 加载到内存 LRU
            chunks = json.loads(row.chunks) if row.chunks else None
            entry = _CacheEntry(
                response_body=row.response_body, chunks=chunks,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                model=row.model, kind=row.kind, expires_at=row.expires_at)
            with self._lock:
                self._lru[cache_key] = entry
                self._evict_if_needed()
                self._hits += 1
                self._note_hit()
            # 异步更新命中统计(不阻塞热路径)
            row.hit_count = (row.hit_count or 0) + 1
            row.last_hit_at = _now()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return entry

        if row and row.expires_at and _utc_from_naive(row.expires_at) <= _now():
            # SQLite 中也过期,删除
            try:
                db.session.delete(row)
                db.session.commit()
            except Exception:
                db.session.rollback()

        with self._lock:
            self._misses += 1
        return None

    def put(self, cache_key: str, kind: str, model: str,
            response_body: str, prompt_tokens: int, completion_tokens: int):
        """写入非流式缓存: 内存 LRU + SQLite"""
        ttl = _get_ttl(kind)
        expires_at = _now() + timedelta(seconds=ttl)

        entry = _CacheEntry(
            response_body=response_body, chunks=None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model, kind=kind, expires_at=expires_at)

        with self._lock:
            self._lru[cache_key] = entry
            self._evict_if_needed()

        # 写 SQLite(持久化)
        try:
            row = db.session.get(ResponseCacheEntry, cache_key)
            if row:
                row.response_body = response_body
                row.chunks = None
                row.prompt_tokens = prompt_tokens
                row.completion_tokens = completion_tokens
                row.expires_at = expires_at
                row.hit_count = 0
                row.last_hit_at = None
            else:
                row = ResponseCacheEntry(
                    cache_key=cache_key, kind=kind, model=model,
                    response_body=response_body, chunks=None,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    expires_at=expires_at, hit_count=0)
                db.session.add(row)
            db.session.commit()
        except Exception:
            db.session.rollback()
        # 超出 cache_max_sqlite 上限时按 LRU 淘汰(独立事务,失败不影响写入)
        self._prune_sqlite()

    def put_stream(self, cache_key: str, kind: str, model: str,
                   chunks: list, prompt_tokens: int, completion_tokens: int):
        """写入流式缓存: 内存 LRU + SQLite（存储 SSE chunks 数组）"""
        ttl = _get_ttl(kind)
        expires_at = _now() + timedelta(seconds=ttl)

        entry = _CacheEntry(
            response_body=None, chunks=chunks,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model, kind=kind, expires_at=expires_at)

        with self._lock:
            self._lru[cache_key] = entry
            self._evict_if_needed()

        # 写 SQLite(持久化,chunks 存为 JSON)
        try:
            chunks_json = json.dumps(chunks, ensure_ascii=False)
            row = db.session.get(ResponseCacheEntry, cache_key)
            if row:
                row.response_body = ""
                row.chunks = chunks_json
                row.prompt_tokens = prompt_tokens
                row.completion_tokens = completion_tokens
                row.expires_at = expires_at
                row.hit_count = 0
                row.last_hit_at = None
            else:
                row = ResponseCacheEntry(
                    cache_key=cache_key, kind=kind, model=model,
                    response_body="", chunks=chunks_json,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                    expires_at=expires_at, hit_count=0)
                db.session.add(row)
            db.session.commit()
        except Exception:
            db.session.rollback()
        # 超出 cache_max_sqlite 上限时按 LRU 淘汰(独立事务,失败不影响写入)
        self._prune_sqlite()

    def record_hit_event(self, cache_key, model, kind, prompt_tokens, completion_tokens, saved_cost):
        """记录缓存命中事件(供趋势图和最近记录查询)"""
        try:
            ev = CacheEvent(
                cache_key=cache_key, model=model, kind=kind,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                saved_cost=saved_cost)
            db.session.add(ev)
            db.session.commit()
        except Exception:
            db.session.rollback()

    # ---------- 管理操作 ----------

    def invalidate(self, cache_key: str):
        with self._lock:
            self._lru.pop(cache_key, None)
        try:
            row = db.session.get(ResponseCacheEntry, cache_key)
            if row:
                db.session.delete(row)
                db.session.commit()
        except Exception:
            db.session.rollback()

    def clear(self):
        """清空全部缓存"""
        with self._lock:
            self._lru.clear()
            self._hits = 0
            self._misses = 0
            self._hourly_hits.clear()
        try:
            ResponseCacheEntry.query.delete(synchronize_session=False)
            CacheEvent.query.delete(synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()

    def cleanup_expired(self):
        """清理过期条目(SQLite + 内存)"""
        now = _now()
        with self._lock:
            expired_keys = [k for k, e in self._lru.items() if now >= e.expires_at]
            for k in expired_keys:
                del self._lru[k]
        try:
            n = ResponseCacheEntry.query.filter(
                ResponseCacheEntry.expires_at < now).delete(synchronize_session=False)
            # 清理超过 7 天的命中事件
            cutoff = now - timedelta(days=7)
            CacheEvent.query.filter(CacheEvent.created_at < cutoff).delete(synchronize_session=False)
            db.session.commit()
            # 同时按容量上限淘汰(防止响应缓存表无界增长)
            self._prune_sqlite()
            return n
        except Exception:
            db.session.rollback()
            return 0

    def warm_up(self):
        """启动时从 SQLite 加载未过期缓存到内存 LRU"""
        now = _now()
        rows = (ResponseCacheEntry.query
                .filter(ResponseCacheEntry.expires_at > now)
                .order_by(ResponseCacheEntry.hit_count.desc())
                .limit(_get_max_memory())
                .all())
        with self._lock:
            for row in rows:
                chunks = json.loads(row.chunks) if row.chunks else None
                self._lru[row.cache_key] = _CacheEntry(
                    response_body=row.response_body, chunks=chunks,
                    prompt_tokens=row.prompt_tokens,
                    completion_tokens=row.completion_tokens,
                    model=row.model, kind=row.kind, expires_at=row.expires_at)
        return len(rows)

    # ---------- 统计 ----------

    def stats(self) -> dict:
        with self._lock:
            hits, misses = self._hits, self._misses
            mem_size = len(self._lru)
        try:
            sqlite_size = ResponseCacheEntry.query.count()
        except Exception:
            sqlite_size = 0
        total = hits + misses
        return {
            "hits": hits, "misses": misses,
            "hit_rate": round(hits / total * 100, 1) if total else 0.0,
            "memory_entries": mem_size,
            "sqlite_entries": sqlite_size,
            "max_memory": _get_max_memory(),
            "max_sqlite": _get_max_sqlite(),
            "enabled": cache_enabled(),
        }

    def hit_trend(self, hours=24) -> list:
        """每小时缓存命中数(从 CacheEvent 聚合)"""
        from sqlalchemy import func
        start = _now() - timedelta(hours=hours)
        rows = (db.session.query(
                    func.strftime("%Y-%m-%dT%H:00", CacheEvent.created_at),
                    func.count(CacheEvent.id),
                    func.coalesce(func.sum(CacheEvent.saved_cost), 0))
                .filter(CacheEvent.created_at >= start)
                .group_by(func.strftime("%Y-%m-%dT%H:00", CacheEvent.created_at))
                .order_by(func.strftime("%Y-%m-%dT%H:00", CacheEvent.created_at))
                .all())
        return [{"hour": r[0], "hits": r[1], "saved_cost": round(float(r[2]), 4)} for r in rows]

    def recent_hits(self, limit=50) -> list:
        """最近缓存命中记录"""
        rows = (CacheEvent.query
                .order_by(CacheEvent.id.desc())
                .limit(min(limit, 200))
                .all())
        now = _now()
        result = []
        for r in rows:
            # 从 SQLite 获取剩余 TTL
            entry = db.session.get(ResponseCacheEntry, r.cache_key)
            ttl_left = 0
            if entry and entry.expires_at:
                remaining = (_utc_from_naive(entry.expires_at) - now).total_seconds()
                ttl_left = max(0, int(remaining))
            # 判断是否为流式缓存
            entry_row = db.session.get(ResponseCacheEntry, r.cache_key)
            is_stream = entry_row and entry_row.chunks is not None
            result.append({
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "model": r.model, "kind": r.kind,
                "is_stream": is_stream,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "saved_cost": r.saved_cost,
                "ttl_left": ttl_left,
            })
        return result

    # ---------- 内部 ----------

    def _evict_if_needed(self):
        """LRU 淘汰: 超出内存上限时移除最久未使用"""
        max_mem = _get_max_memory()
        while len(self._lru) > max_mem:
            self._lru.popitem(last=False)

    def _prune_sqlite(self):
        """SQLite 容量上限淘汰: 超出 cache_max_sqlite 时按 LRU 淘汰最旧条目。
        SQLite 中 last_hit_at 为 NULL(从未命中)的条目优先淘汰(ASC 排序 NULL 在前),
        其余按最后命中时间旧 -> 新依次淘汰;同时同步清掉内存 LRU 中的对应条目。"""
        max_n = _get_max_sqlite()
        try:
            total = ResponseCacheEntry.query.count()
            if total <= max_n:
                return
            excess = total - max_n
            keys = [r[0] for r in (
                db.session.query(ResponseCacheEntry.cache_key)
                .order_by(ResponseCacheEntry.last_hit_at.asc(),
                          ResponseCacheEntry.created_at.asc())
                .limit(excess).all())]
            if not keys:
                return
            ResponseCacheEntry.query.filter(
                ResponseCacheEntry.cache_key.in_(keys)).delete(synchronize_session=False)
            db.session.commit()
            with self._lock:
                for k in keys:
                    self._lru.pop(k, None)
        except Exception:
            db.session.rollback()

    def _note_hit(self):
        """记录每小时命中数(内存级,用于快速统计)"""
        hour_key = _now().strftime("%Y-%m-%dT%H:00")
        self._hourly_hits[hour_key] = self._hourly_hits.get(hour_key, 0) + 1


# 全局单例
cache = ResponseCache()
