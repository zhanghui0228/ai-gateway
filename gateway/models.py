"""ORM 模型"""
import json
import secrets
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .db import db


def utcnow():
    return datetime.now(timezone.utc)


def iso_utc(dt):
    """把 datetime 安全地序列化为带 Z 的 UTC ISO 字符串。
    SQLite 读取后 tzinfo 会丢失(naive),此时按 UTC 处理并追加 Z,
    让前端 new Date() 能正确解析为 UTC 再转本地时区。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class JSONText(db.TypeDecorator):
    """SQLite 下用 TEXT 存 JSON"""
    impl = db.Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value, ensure_ascii=False) if value is not None else "[]"

    def process_result_value(self, value, dialect):
        try:
            return json.loads(value) if value else []
        except (TypeError, ValueError):
            return []


class Setting(db.Model):
    """键值系统设置"""
    __tablename__ = "settings"
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=None):
        row = db.session.get(Setting, key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = db.session.get(Setting, key)
        if row:
            row.value = str(value)
        else:
            db.session.add(Setting(key=key, value=str(value)))
        db.session.commit()


class Admin(db.Model):
    """管理员账号(单账号)"""
    __tablename__ = "admins"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Channel(db.Model):
    """上游渠道"""
    __tablename__ = "channels"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    preset = db.Column(db.String(64), default="custom")      # 预设厂商标识
    adapter = db.Column(db.String(32), nullable=False, default="openai_compat")
    base_url = db.Column(db.String(512), nullable=False)
    api_key = db.Column(db.String(1024), default="")          # 多个 key 用英文逗号分隔轮询
    models = db.Column(JSONText, default=list)                # 对外支持的模型名列表
    model_mapping = db.Column(JSONText, default=dict)         # 对外名 -> 上游实际名 {"gpt-4o":"x"}
    weight = db.Column(db.Integer, default=1)                 # 同优先级内权重
    priority = db.Column(db.Integer, default=0)               # 数值越大优先级越高
    tier = db.Column(db.Integer, default=0)                   # 梯队: 1=主渠道,2=备用,3=兜底,0=未分类
    enabled = db.Column(db.Boolean, default=True)
    # 每渠道独立代理
    proxy_url = db.Column(db.String(512), default="")         # http://user:pass@host:port 或 socks5://...
    # 熔断状态(运行时持久化,便于重启后保留)
    fail_streak = db.Column(db.Integer, default=0)
    breaker_until = db.Column(db.String(32), default="")      # ISO 时间,熔断截止
    # 单渠道价格覆盖 {"model": {"input": x, "output": y}} 单位:元/百万token,为空走全局价
    pricing_override = db.Column(JSONText, default=dict)
    timeout = db.Column(db.Integer, default=0)                # 0 = 使用全局默认
    note = db.Column(db.String(512), default="")
    # 自定义请求头:部分中转站做客户端指纹校验,需伪装官方客户端 UA 才能通过
    user_agent = db.Column(db.String(256), default="")
    extra_headers = db.Column(JSONText, default=dict)          # 额外请求头 {"X-Foo":"bar"}

    # 预设自定义字段值(如 organization_id / project_id 等,由预设定义)
    custom_fields = db.Column(JSONText, default=dict)
    # 额外的 azure 参数
    azure_api_version = db.Column(db.String(32), default="2024-10-21")

    # L1 定时探测结果(免费模型列表探测,零 token 消耗)
    probe_ok = db.Column(db.Boolean, nullable=True)   # None = 尚未探测
    probe_at = db.Column(db.String(32), default="")
    probe_latency = db.Column(db.Integer, default=0)
    probe_error = db.Column(db.String(256), default="")
    # 探测方式:models=模型列表端点(免费) / chat=聊天端点(max_tokens=1,消耗极少) / off=不探测
    probe_mode = db.Column(db.String(16), default="models")
    # 各模型深度测试状态 {"model": {"ok": bool, "status": int, "latency_ms": int, "error": str, "tested_at": str}}
    model_status = db.Column(JSONText, default=dict)

    created_at = db.Column(db.DateTime, default=utcnow)

    def real_model(self, name):
        mapping = self.model_mapping or {}
        return mapping.get(name, name)

    def current_api_key(self):
        keys = [k.strip() for k in (self.api_key or "").split(",") if k.strip()]
        if not keys:
            return ""
        # 简单轮询:按失败次数取模,保证故障转移时换 key
        return keys[self.fail_streak % len(keys)]

    @property
    def tier_label(self):
        return {1: "第一梯队", 2: "第二梯队", 3: "第三梯队"}.get(self.tier, "未分类")

    @staticmethod
    def tier_priority(tier):
        """梯队默认优先级映射: 第一梯队=100, 第二梯队=50, 第三梯队=10"""
        return {1: 100, 2: 50, 3: 10}.get(tier or 0, 0)

    def to_dict(self, mask_key=True):
        d = {
            "id": self.id, "name": self.name, "preset": self.preset,
            "adapter": self.adapter, "base_url": self.base_url,
            "api_key": self.api_key, "models": self.models or [],
            "model_mapping": self.model_mapping or {},
            "weight": self.weight, "priority": self.priority,
            "tier": self.tier or 0,
            "enabled": self.enabled, "proxy_url": self.proxy_url,
            "fail_streak": self.fail_streak, "breaker_until": self.breaker_until,
            "pricing_override": self.pricing_override or {},
            "timeout": self.timeout, "note": self.note,
            "azure_api_version": self.azure_api_version,
            "probe_ok": self.probe_ok, "probe_at": self.probe_at,
            "probe_latency": self.probe_latency, "probe_error": self.probe_error,
            "probe_mode": self.probe_mode or "models",
            "model_status": self.model_status or {},
            "user_agent": self.user_agent or "", "extra_headers": self.extra_headers or {},
            "custom_fields": self.custom_fields or {},
            "created_at": iso_utc(self.created_at),
        }
        if mask_key and d["api_key"]:
            keys = [k.strip() for k in d["api_key"].split(",") if k.strip()]
            d["api_key"] = ",".join(k[:6] + "***" + k[-4:] if len(k) > 12 else "***" for k in keys)
        return d


class ApiKey(db.Model):
    """对外发放的 API Key"""
    __tablename__ = "api_keys"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), default="")
    key = db.Column(db.String(128), unique=True, nullable=False, index=True)
    quota_tokens = db.Column(db.BigInteger, default=-1)       # -1 无限
    used_tokens = db.Column(db.BigInteger, default=0)
    allowed_models = db.Column(JSONText, default=list)        # 空 = 不限制
    expires_at = db.Column(db.DateTime, nullable=True)        # 空 = 永不过期
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    @staticmethod
    def generate():
        return "sk-gw-" + secrets.token_hex(24)

    @property
    def expired(self):
        if not self.expires_at:
            return False
        exp = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        return utcnow() >= exp

    def model_allowed(self, model):
        allowed = self.allowed_models or []
        return not allowed or model in allowed

    def to_dict(self, mask=True):
        return {
            "id": self.id, "name": self.name,
            "key": (self.key[:10] + "***" + self.key[-4:]) if mask else self.key,
            "quota_tokens": self.quota_tokens, "used_tokens": self.used_tokens,
            "allowed_models": self.allowed_models or [],
            "expires_at": iso_utc(self.expires_at),
            "enabled": self.enabled,
            "created_at": iso_utc(self.created_at),
        }


class ModelPrice(db.Model):
    """全局模型单价(元/百万 token)+ 元数据"""
    __tablename__ = "model_prices"
    model = db.Column(db.String(128), primary_key=True)
    input_price = db.Column(db.Float, default=0.0)
    output_price = db.Column(db.Float, default=0.0)
    currency = db.Column(db.String(8), default="CNY")
    context_window = db.Column(db.Integer, nullable=True)   # 上下文窗口
    max_output = db.Column(db.Integer, nullable=True)       # 最大输出 tokens
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class CallLog(db.Model):
    """调用日志:逐次请求的完整记录(含请求/响应内容,可配置截断与保留期)"""
    __tablename__ = "call_logs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    request_id = db.Column(db.String(32), index=True)      # 请求追踪 ID
    key_id = db.Column(db.Integer, index=True)
    key_name = db.Column(db.String(128), default="")
    channel_id = db.Column(db.Integer, index=True)
    channel_name = db.Column(db.String(128), default="")
    endpoint = db.Column(db.String(32), default="")        # chat/completions/embeddings/images
    model_requested = db.Column(db.String(128), default="", index=True)  # 客户端请求的模型(auto 时为 auto)
    model_actual = db.Column(db.String(128), default="")                 # 实际调用的模型
    is_stream = db.Column(db.Boolean, default=False)
    status_code = db.Column(db.Integer, default=0)
    success = db.Column(db.Boolean, default=False, index=True)
    latency_ms = db.Column(db.Integer, default=0)
    retries = db.Column(db.Integer, default=0)
    prompt_tokens = db.Column(db.BigInteger, default=0)
    completion_tokens = db.Column(db.BigInteger, default=0)
    total_tokens = db.Column(db.BigInteger, default=0)
    cache_read_tokens = db.Column(db.BigInteger, default=0)
    cost = db.Column(db.Float, default=0.0)
    client_ip = db.Column(db.String(64), default="")
    user_agent = db.Column(db.String(256), default="")
    request_body = db.Column(db.Text, default="")          # 截断后的请求体
    response_body = db.Column(db.Text, default="")         # 截断后的响应内容
    cache_hit = db.Column(db.Boolean, default=False)       # 是否命中响应缓存
    error = db.Column(db.String(512), default="")

    def summary(self):
        return {
            "id": self.id,
            "created_at": iso_utc(self.created_at),
            "request_id": self.request_id, "key_id": self.key_id, "key_name": self.key_name,
            "channel_id": self.channel_id, "channel_name": self.channel_name,
            "endpoint": self.endpoint, "model_requested": self.model_requested,
            "model_actual": self.model_actual, "is_stream": self.is_stream,
            "status_code": self.status_code, "success": self.success,
            "latency_ms": self.latency_ms, "retries": self.retries,
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens, "cache_read_tokens": self.cache_read_tokens,
            "cost": self.cost, "cache_hit": self.cache_hit,
            "client_ip": self.client_ip, "user_agent": self.user_agent,
            "error": self.error,
            "req_len": len(self.request_body or ""), "resp_len": len(self.response_body or ""),
        }

    def detail(self):
        d = self.summary()
        d["request_body"] = self.request_body or ""
        d["response_body"] = self.response_body or ""
        return d


class UsageLog(db.Model):
    """调用明细"""
    __tablename__ = "usage_logs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    key_id = db.Column(db.Integer, index=True)
    key_name = db.Column(db.String(128), default="")
    channel_id = db.Column(db.Integer, index=True)
    channel_name = db.Column(db.String(128), default="")
    model = db.Column(db.String(128), index=True)
    prompt_tokens = db.Column(db.BigInteger, default=0)
    completion_tokens = db.Column(db.BigInteger, default=0)
    total_tokens = db.Column(db.BigInteger, default=0)
    cache_read_tokens = db.Column(db.BigInteger, default=0)       # 命中缓存 tokens
    cache_creation_tokens = db.Column(db.BigInteger, default=0)   # 写入缓存 tokens
    cost = db.Column(db.Float, default=0.0)
    latency_ms = db.Column(db.Integer, default=0)
    status_code = db.Column(db.Integer, default=0)
    success = db.Column(db.Boolean, default=False, index=True)
    is_stream = db.Column(db.Boolean, default=False)
    estimated = db.Column(db.Boolean, default=False)          # usage 为估算
    retries = db.Column(db.Integer, default=0)                # 故障转移次数
    error = db.Column(db.String(512), default="")

    def to_dict(self):
        return {
            "id": self.id, "created_at": iso_utc(self.created_at),
            "key_id": self.key_id, "key_name": self.key_name,
            "channel_id": self.channel_id, "channel_name": self.channel_name,
            "model": self.model, "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens, "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cost": self.cost, "latency_ms": self.latency_ms,
            "status_code": self.status_code, "success": self.success,
            "is_stream": self.is_stream, "estimated": self.estimated,
            "retries": self.retries, "error": self.error,
        }


class Preset(db.Model):
    """预设厂商(可动态管理,运行时存数据库,首次启动从 presets.py 种子写入)"""
    __tablename__ = "presets"
    id = db.Column(db.String(64), primary_key=True)            # 如 openai / deepseek / agnes
    name = db.Column(db.String(128), nullable=False)            # 显示名
    adapter = db.Column(db.String(32), nullable=False, default="openai_compat")
    base_url = db.Column(db.String(512), default="")
    models = db.Column(JSONText, default=list)                  # 常用模型列表
    prices = db.Column(JSONText, default=dict)                  # 参考单价 {"model": (输入,输出)}
    probe_mode = db.Column(db.String(16), default="models")     # models / chat / off
    user_agent = db.Column(db.String(256), default="")          # 部分站点需伪装 UA
    needs_proxy = db.Column(db.Boolean, default=False)          # 是否需代理访问
    local = db.Column(db.Boolean, default=False)                # 本地服务(如 Ollama)
    note = db.Column(db.String(512), default="")                # 备注说明
    key_url = db.Column(db.String(512), default="")             # 快速获取 API Key 的链接
    # 两个自定义字段定义(如 Organization ID / Project ID / Tenant 等)
    custom_1_label = db.Column(db.String(64), default="")      # 字段1名称
    custom_1_key = db.Column(db.String(64), default="")        # 字段1键
    custom_1_placeholder = db.Column(db.String(128), default="")
    custom_2_label = db.Column(db.String(64), default="")
    custom_2_key = db.Column(db.String(64), default="")
    custom_2_placeholder = db.Column(db.String(128), default="")
    is_built_in = db.Column(db.Boolean, default=False)          # 内置预设(种子来源,可编辑不可删除)
    sort_order = db.Column(db.Integer, default=0)               # 排序权重
    created_at = db.Column(db.DateTime, default=utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "adapter": self.adapter,
            "base_url": self.base_url, "models": self.models or [],
            "prices": self.prices or {},
            "probe_mode": self.probe_mode or "models",
            "user_agent": self.user_agent or "",
            "needs_proxy": self.needs_proxy, "local": self.local,
            "note": self.note or "", "key_url": self.key_url or "",
            "custom_1_label": self.custom_1_label or "",
            "custom_1_key": self.custom_1_key or "",
            "custom_1_placeholder": self.custom_1_placeholder or "",
            "custom_2_label": self.custom_2_label or "",
            "custom_2_key": self.custom_2_key or "",
            "custom_2_placeholder": self.custom_2_placeholder or "",
            "is_built_in": self.is_built_in,
            "sort_order": self.sort_order or 0,
        }


# ---------- 响应缓存 ----------

class ResponseCacheEntry(db.Model):
    """响应缓存持久化存储（非流式存 response_body,流式存 chunks JSON）"""
    __tablename__ = "response_cache"
    cache_key = db.Column(db.String(64), primary_key=True)   # SHA256 hex
    kind = db.Column(db.String(16), default="")              # chat/completions/embeddings
    model = db.Column(db.String(128), default="")
    response_body = db.Column(db.Text)                        # 非流式: JSON 响应体
    chunks = db.Column(db.Text)                               # 流式: SSE data 字符串数组(JSON)
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=utcnow)
    expires_at = db.Column(db.DateTime, index=True)           # TTL 过期时间
    hit_count = db.Column(db.Integer, default=0)
    last_hit_at = db.Column(db.DateTime)


class CacheEvent(db.Model):
    """缓存命中事件记录(供趋势图和最近记录查询)"""
    __tablename__ = "cache_events"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    cache_key = db.Column(db.String(64), index=True)
    model = db.Column(db.String(128), default="")
    kind = db.Column(db.String(16), default="")
    prompt_tokens = db.Column(db.Integer, default=0)
    completion_tokens = db.Column(db.Integer, default=0)
    saved_cost = db.Column(db.Float, default=0.0)
