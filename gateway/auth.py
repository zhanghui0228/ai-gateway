"""鉴权:管理员 session + 对外 API Key 校验"""
import logging
from functools import wraps

from flask import jsonify, request, session

import config
from .models import Admin, ApiKey, Setting

logger = logging.getLogger(__name__)


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin_id"):
            return jsonify({"error": "未登录"}), 401
        return f(*args, **kwargs)
    return wrapper


def bearer_token():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return ""


def api_key_required(f):
    """对外 API 鉴权 + 模型白名单 + 额度预检"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = bearer_token()
        if not token:
            return jsonify({"error": {"message": "缺少 API Key", "type": "auth_error"}}), 401
        row = ApiKey.query.filter_by(key=token).first()
        if not row or not row.enabled:
            return jsonify({"error": {"message": "无效 API Key", "type": "auth_error"}}), 401
        if row.expired:
            return jsonify({"error": {"message": "API Key 已过期", "type": "auth_error"}}), 401
        ok, remaining = True, -1
        try:
            from . import quota
            ok, remaining = quota.check_quota(row)
        except Exception as e:
            logger.error("配额检查异常,拒绝请求: %s", e, exc_info=True)
            return jsonify({"error": {"message": "配额检查失败,请稍后重试", "type": "quota_error",
                                       "code": "quota_check_failed"}}), 503
        if not ok:
            return jsonify({"error": {"message": "额度已耗尽", "type": "quota_exceeded",
                                       "code": "insufficient_quota"}}), 429
        # 速率限制检查
        try:
            from . import ratelimit
            rpm = int(Setting.get("rate_limit_rpm", "60"))
            rph = int(Setting.get("rate_limit_rph", "1000"))
            if rpm > 0 or rph > 0:
                allowed, rate_info = ratelimit.check_rate(
                    token, rpm or 999999, rph or 999999)
                if not allowed:
                    resp = jsonify({"error": {"message": "请求过于频繁,请稍后重试",
                                              "type": "rate_limit_error",
                                              "code": "rate_limited"}})
                    resp.status_code = 429
                    resp.headers["X-RateLimit-Limit"] = str(rate_info.get("limit_rpm", ""))
                    resp.headers["X-RateLimit-Remaining"] = "0"
                    resp.headers["X-RateLimit-Reset"] = str(rate_info.get("reset_rpm", ""))
                    resp.headers["Retry-After"] = str(rate_info.get("retry_after", 60))
                    return resp
        except Exception:
            pass  # 限流检查失败不阻塞请求
        request.gw_api_key = row
        return f(*args, **kwargs)
    return wrapper


def init_admin(app):
    """首次启动初始化管理员"""
    with app.app_context():
        from .db import db
        if Admin.query.count() == 0:
            admin = Admin(username=app.config["DEFAULT_ADMIN_USER"])
            admin.set_password(app.config["DEFAULT_ADMIN_PASSWORD"])
            db.session.add(admin)
            db.session.commit()


def default_settings():
    """初始化系统设置默认值(不覆盖已有)"""
    defaults = {
        "default_timeout": "120", "max_retry": "3",
        "breaker_threshold": "5", "breaker_cooldown": "60",
        "probe_interval": "300",
        "auto_timeout": "120", "auto_max_models": "5",
        "log_bodies": "1", "log_body_max": "2000", "log_retention_days": "7",
        "cache_enabled": "1", "cache_stream": "1", "cache_ttl": "300",
        "cache_ttl_deterministic": "3600",
        "cache_max_memory": "500", "cache_max_sqlite": "10000",
        "rate_limit_rpm": "60", "rate_limit_rph": "1000",
        "login_max_attempts": "5", "login_lockout_duration": "300",
        "webhook_enabled": "0", "webhook_url": "",
        "webhook_on_channel_fail": "1", "webhook_on_quota_alert": "1",
        "update_enabled": "1", "update_repo": config.UPDATE_REPO,
        "update_repo_fallback": config.UPDATE_REPO_FALLBACK,
        "update_branch": config.UPDATE_BRANCH, "update_mode": "direct",
        "update_check_interval": "6", "update_auto_restart": "1",
    }
    for k, v in defaults.items():
        if Setting.get(k) is None:
            Setting.set(k, v)


def fix_settings():
    """自愈修正:将已知错误/过期的设置值更新为正确值(仅修正,不影响用户自定义)"""
    # 历史错误:早期版本将备用源写成 zhanghui0228(应为 zhh0228)
    wrong = "https://gitcode.com/zhanghui0228/ai-gateway.git"
    correct = config.UPDATE_REPO_FALLBACK
    cur = Setting.get("update_repo_fallback")
    if cur and cur.strip() == wrong:
        Setting.set("update_repo_fallback", correct)
    # 缓存默认值升级: 早期版本 cache_max_memory=200, 现默认 500(老库未自定义才迁移)
    if Setting.get("cache_max_memory") == "200":
        Setting.set("cache_max_memory", "500")
    # 老库补确定性 TTL 设置(新建库由 default_settings 写入)
    if Setting.get("cache_ttl_deterministic") is None:
        Setting.set("cache_ttl_deterministic", "3600")
