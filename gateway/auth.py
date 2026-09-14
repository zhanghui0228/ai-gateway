"""鉴权:管理员 session + 对外 API Key 校验"""
from functools import wraps

from flask import jsonify, request, session

import config
from .models import Admin, ApiKey, Setting


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
        except Exception:
            pass
        if not ok:
            return jsonify({"error": {"message": "额度已耗尽", "type": "quota_exceeded",
                                       "code": "insufficient_quota"}}), 429
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
        "cache_max_memory": "200", "cache_max_sqlite": "10000",
        "update_enabled": "1", "update_repo": config.UPDATE_REPO,
        "update_repo_fallback": config.UPDATE_REPO_FALLBACK,
        "update_branch": config.UPDATE_BRANCH, "update_mode": "direct",
        "update_check_interval": "6", "update_auto_restart": "1",
    }
    for k, v in defaults.items():
        if Setting.get(k) is None:
            Setting.set(k, v)
