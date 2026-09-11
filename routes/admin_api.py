"""管理控制台 API"""
import threading
import httpx
from flask import (Blueprint, current_app, jsonify, request, session)

import config
from gateway import balancer, events, pricing, stats
from gateway.auth import admin_required, default_settings
from gateway.db import db
from gateway.models import Admin, ApiKey, Channel, ModelPrice, Preset, Setting, UsageLog
from gateway.presets import PRESETS, preset_list, seed_presets
from gateway.proxy import drop_channel
from gateway.adapters.registry import get_adapter

admin_bp = Blueprint("admin", __name__, url_prefix="/admin/api")


# ---------- 登录 ----------
@admin_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    admin = Admin.query.first()
    if not admin or not admin.check_password(data.get("password") or ""):
        return jsonify({"error": "密码错误"}), 401
    session["admin_id"] = admin.id
    session.permanent = True
    return jsonify({"ok": True, "username": admin.username})


@admin_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@admin_bp.route("/me", methods=["GET"])
def me():
    if session.get("admin_id"):
        admin = db.session.get(Admin, session["admin_id"])
        if admin:
            return jsonify({"logged_in": True, "username": admin.username})
    return jsonify({"logged_in": False})


@admin_bp.route("/password", methods=["POST"])
@admin_required
def change_password():
    data = request.get_json(silent=True) or {}
    admin = db.session.get(Admin, session["admin_id"])
    if not admin.check_password(data.get("old") or ""):
        return jsonify({"error": "原密码错误"}), 400
    if not (data.get("new") or "") or len(data["new"]) < 6:
        return jsonify({"error": "新密码至少6位"}), 400
    admin.set_password(data["new"])
    db.session.commit()
    return jsonify({"ok": True})


# ---------- 预设(数据库 CRUD) ----------
@admin_bp.route("/presets", methods=["GET"])
@admin_required
def presets():
    return jsonify(preset_list())


@admin_bp.route("/presets", methods=["POST"])
@admin_required
def create_preset():
    data = request.get_json(silent=True) or {}
    if not data.get("id") or not data.get("name"):
        return jsonify({"error": "id 和 name 必填"}), 400
    if db.session.get(Preset, data["id"]):
        return jsonify({"error": "该 id 已存在"}), 409
    pr = Preset(
        id=data["id"].strip(), name=data["name"].strip(),
        adapter=data.get("adapter", "openai_compat"),
        base_url=data.get("base_url", ""),
        models=data.get("models") or [],
        prices=data.get("prices") or {},
        probe_mode=data.get("probe_mode", "models"),
        user_agent=data.get("user_agent", ""),
        needs_proxy=bool(data.get("needs_proxy")),
        local=bool(data.get("local")),
        note=data.get("note", ""),
        key_url=data.get("key_url", ""),
        custom_1_label=data.get("custom_1_label", ""),
        custom_1_key=data.get("custom_1_key", ""),
        custom_1_placeholder=data.get("custom_1_placeholder", ""),
        custom_2_label=data.get("custom_2_label", ""),
        custom_2_key=data.get("custom_2_key", ""),
        custom_2_placeholder=data.get("custom_2_placeholder", ""),
        is_built_in=False,
        sort_order=int(data.get("sort_order", 0)),
    )
    db.session.add(pr)
    db.session.commit()
    return jsonify(pr.to_dict())


@admin_bp.route("/presets/<pid>", methods=["PUT"])
@admin_required
def update_preset(pid):
    pr = db.session.get(Preset, pid)
    if not pr:
        return jsonify({"error": "预设不存在"}), 404
    data = request.get_json(silent=True) or {}
    for field in ("name", "adapter", "base_url", "probe_mode", "user_agent",
                  "note", "key_url",
                  "custom_1_label", "custom_1_key", "custom_1_placeholder",
                  "custom_2_label", "custom_2_key", "custom_2_placeholder"):
        if field in data:
            setattr(pr, field, data[field] or "")
    if "models" in data:
        pr.models = data["models"] or []
    if "prices" in data:
        pr.prices = data["prices"] or {}
    if "needs_proxy" in data:
        pr.needs_proxy = bool(data["needs_proxy"])
    if "local" in data:
        pr.local = bool(data["local"])
    if "sort_order" in data:
        pr.sort_order = int(data["sort_order"] or 0)
    db.session.commit()
    return jsonify(pr.to_dict())


@admin_bp.route("/presets/<pid>", methods=["DELETE"])
@admin_required
def delete_preset(pid):
    pr = db.session.get(Preset, pid)
    if not pr:
        return jsonify({"error": "预设不存在"}), 404
    if pr.is_built_in:
        return jsonify({"error": "内置预设不可删除,可改为停用或编辑"}), 400
    # 检查是否有渠道正在引用该 preset
    ref = Channel.query.filter_by(preset=pid).first()
    if ref:
        return jsonify({"error": f"已有渠道「{ref.name}」引用此预设,请先修改该渠道"}), 400
    db.session.delete(pr)
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/presets/seed", methods=["POST"])
@admin_required
def reseed_presets():
    """重新种子内置预设(仅填充缺失的,不覆盖已存在的)"""
    n = seed_presets()
    return jsonify({"ok": True, "seeded": n})


# ---------- 渠道 ----------
@admin_bp.route("/channels", methods=["GET"])
@admin_required
def list_channels():
    return jsonify([_channel_view(c) for c in Channel.query.order_by(Channel.priority.desc()).all()])


@admin_bp.route("/channels", methods=["POST"])
@admin_required
def create_channel():
    data = request.get_json(silent=True) or {}
    if not data.get("name") or not data.get("base_url"):
        return jsonify({"error": "name 和 base_url 必填"}), 400
    ch = Channel(
        name=data["name"], preset=data.get("preset", "custom"),
        adapter=data.get("adapter", "openai_compat"), base_url=data["base_url"].strip(),
        api_key=data.get("api_key", ""), models=data.get("models") or [],
        model_mapping=data.get("model_mapping") or {},
        weight=int(data.get("weight", 1)), priority=int(data.get("priority", 0)),
        enabled=bool(data.get("enabled", True)), proxy_url=data.get("proxy_url", ""),
        pricing_override=data.get("pricing_override") or {},
        timeout=int(data.get("timeout", 0)), note=data.get("note", ""),
        probe_mode=data.get("probe_mode") or "models",
        user_agent=data.get("user_agent", ""),
        extra_headers=data.get("extra_headers") or {},
        custom_fields=data.get("custom_fields") or {},
        azure_api_version=data.get("azure_api_version", "2024-10-21"))
    db.session.add(ch)
    db.session.commit()
    return jsonify(_channel_view(ch))


@admin_bp.route("/channels/<int:cid>", methods=["PUT"])
@admin_required
def update_channel(cid):
    ch = db.session.get(Channel, cid)
    if not ch:
        return jsonify({"error": "渠道不存在"}), 404
    data = request.get_json(silent=True) or {}
    old_proxy = ch.proxy_url
    for field in ("name", "preset", "adapter", "base_url", "api_key", "proxy_url", "note",
                  "azure_api_version", "probe_mode", "user_agent"):
        if field in data:
            val = data[field]
            # 掩码 Key(如 sk-ab***cd)视为"未修改",保持原值
            if field == "api_key" and val and "***" in val:
                continue
            setattr(ch, field, val or "" if field != "name" else val)
    if "extra_headers" in data:
        ch.extra_headers = data["extra_headers"] or {}
    if "custom_fields" in data:
        ch.custom_fields = data["custom_fields"] or {}
    for field in ("models", "model_mapping", "pricing_override"):
        if field in data:
            setattr(ch, field, data[field] or [])
    for field, cast in (("weight", int), ("priority", int), ("timeout", int)):
        if field in data:
            setattr(ch, field, cast(data[field] or 0))
    if "enabled" in data:
        ch.enabled = bool(data["enabled"])
        if ch.enabled:  # 手动重新启用时清空熔断状态
            ch.fail_streak, ch.breaker_until = 0, ""
    db.session.commit()
    if old_proxy != ch.proxy_url:
        drop_channel(ch.id)
    return jsonify(_channel_view(ch))


@admin_bp.route("/channels/<int:cid>", methods=["DELETE"])
@admin_required
def delete_channel(cid):
    ch = db.session.get(Channel, cid)
    if not ch:
        return jsonify({"error": "渠道不存在"}), 404
    db.session.delete(ch)
    db.session.commit()
    drop_channel(cid)
    return jsonify({"ok": True})


@admin_bp.route("/channels/<int:cid>/test", methods=["POST"])
@admin_required
def test_channel(cid):
    """渠道深度测试:逐一探测渠道绑定的所有模型(每个模型发 max_tokens=1 的 chat 请求)"""
    from gateway import probe as probe_mod
    ch = db.session.get(Channel, cid)
    if not ch:
        return jsonify({"error": "渠道不存在"}), 404
    models = ch.models or []
    if not models:
        return jsonify({"ok": False, "error": "渠道未配置模型,无法深度测试"}), 200

    # 逐一探测所有模型(最多前 10 个)
    results = probe_mod.probe_channel_models(ch, max_models=10)

    # 汇总:任意一个模型 ok 则渠道视为在线
    any_ok = any(r.get("ok") for r in results.values())
    ok_count = sum(1 for r in results.values() if r.get("ok"))

    if any_ok:
        balancer.note_success(ch)
    else:
        balancer.note_failure(ch)

    # 持久化 per-model 探测结果
    ch.model_status = results
    db.session.commit()

    return jsonify({
        "ok": any_ok,
        "summary": f"{ok_count}/{len(results)} 模型可用",
        "models": results,
    }), 200


@admin_bp.route("/channels/<int:cid>/reset_breaker", methods=["POST"])
@admin_required
def reset_breaker(cid):
    ch = db.session.get(Channel, cid)
    if not ch:
        return jsonify({"error": "渠道不存在"}), 404
    ch.fail_streak, ch.breaker_until = 0, ""
    db.session.commit()
    return jsonify({"ok": True})


def _channel_view(ch):
    d = ch.to_dict()
    state, cooldown = balancer.breaker_info(ch)
    d["breaker_state"] = state
    d["breaker_cooldown"] = int(cooldown)
    return d


# ---------- 模型降级状态 ----------
@admin_bp.route("/model_degradation", methods=["GET"])
@admin_required
def list_model_degradation():
    """返回当前 auto 模式模型降级状态列表"""
    from gateway import model_degradation
    return jsonify(model_degradation.get_status())


# ---------- 模型状态总览 ==========
@admin_bp.route("/model_status", methods=["GET"])
@admin_required
def model_status_api():
    """按模型维度汇总各渠道的可用状态(模型 × 渠道矩阵)"""
    channels = Channel.query.order_by(Channel.priority.desc(), Channel.id).all()
    by_model = {}
    chan_list = []
    for ch in channels:
        state, _ = balancer.breaker_info(ch)
        chan_list.append({"id": ch.id, "name": ch.name, "enabled": ch.enabled,
                          "breaker": state, "models": ch.models or [],
                          "priority": ch.priority or 0, "weight": ch.weight or 1})
        for m in (ch.models or []):
            entry = {"channel_id": ch.id, "channel_name": ch.name,
                     "enabled": ch.enabled, "breaker": state}
            ms = (ch.model_status or {}).get(m)
            if ms:
                entry.update(ms)
            else:
                entry["ok"] = None  # 未探测
            by_model.setdefault(m, []).append(entry)
    return jsonify({
        "models": sorted(by_model.keys()),
        "channels": chan_list,
        "status": by_model,
    })


@admin_bp.route("/model_degradation/reset", methods=["POST"])
@admin_required
def reset_model_degradation():
    """手动清除模型降级状态。body: {"model": "xxx"} 清除指定,无 body 清除全部"""
    from gateway import model_degradation
    data = request.get_json(silent=True) or {}
    model_degradation.reset(data.get("model"))
    return jsonify({"ok": True})


# ---------- API Key ----------
@admin_bp.route("/keys", methods=["GET"])
@admin_required
def list_keys():
    return jsonify([k.to_dict() for k in ApiKey.query.order_by(ApiKey.id.desc()).all()])


@admin_bp.route("/keys", methods=["POST"])
@admin_required
def create_key():
    data = request.get_json(silent=True) or {}
    key = ApiKey(name=data.get("name", ""), key=ApiKey.generate(),
                 quota_tokens=int(data.get("quota_tokens", -1)),
                 allowed_models=data.get("allowed_models") or [],
                 enabled=True)
    if data.get("expires_at"):
        from datetime import datetime
        try:
            key.expires_at = datetime.fromisoformat(data["expires_at"])
        except ValueError:
            return jsonify({"error": "expires_at 格式无效"}), 400
    db.session.add(key)
    db.session.commit()
    return jsonify(key.to_dict(mask=False))


@admin_bp.route("/keys/<int:kid>", methods=["PUT"])
@admin_required
def update_key(kid):
    key = db.session.get(ApiKey, kid)
    if not key:
        return jsonify({"error": "Key 不存在"}), 404
    data = request.get_json(silent=True) or {}
    if "name" in data:
        key.name = data["name"]
    if "quota_tokens" in data:
        key.quota_tokens = int(data["quota_tokens"])
    if "used_tokens" in data:
        key.used_tokens = int(data["used_tokens"])
    if "allowed_models" in data:
        key.allowed_models = data["allowed_models"] or []
    if "enabled" in data:
        key.enabled = bool(data["enabled"])
    if "expires_at" in data:
        if data["expires_at"]:
            from datetime import datetime
            try:
                key.expires_at = datetime.fromisoformat(data["expires_at"])
            except ValueError:
                return jsonify({"error": "expires_at 格式无效"}), 400
        else:
            key.expires_at = None
    db.session.commit()
    return jsonify(key.to_dict())


@admin_bp.route("/keys/<int:kid>", methods=["DELETE"])
@admin_required
def delete_key(kid):
    key = db.session.get(ApiKey, kid)
    if not key:
        return jsonify({"error": "Key 不存在"}), 404
    db.session.delete(key)
    db.session.commit()
    return jsonify({"ok": True})


# ---------- 模型单价 ----------
@admin_bp.route("/prices", methods=["GET"])
@admin_required
def list_prices():
    return jsonify([{"model": p.model, "input_price": p.input_price,
                     "output_price": p.output_price, "currency": p.currency,
                     "context_window": p.context_window, "max_output": p.max_output}
                    for p in ModelPrice.query.order_by(ModelPrice.model).all()])


@admin_bp.route("/prices", methods=["POST"])
@admin_required
def upsert_prices():
    data = request.get_json(silent=True) or {}
    only_missing = bool(data.get("only_missing"))
    items = data.get("items") if isinstance(data.get("items"), list) else [data]
    for it in items:
        if not it.get("model"):
            continue
        pricing.upsert_price(it["model"],
                             input_price=float(it.get("input_price") or 0) if it.get("input_price") is not None else None,
                             output_price=float(it.get("output_price") or 0) if it.get("output_price") is not None else None,
                             currency=it.get("currency"),
                             context_window=it.get("context_window"),
                             max_output=it.get("max_output"),
                             only_missing=only_missing)
    return jsonify({"ok": True})


@admin_bp.route("/prices/<path:model>", methods=["DELETE"])
@admin_required
def delete_price(model):
    p = db.session.get(ModelPrice, model)
    if p:
        db.session.delete(p)
        db.session.commit()
        pricing.refresh_cache()
    return jsonify({"ok": True})


@admin_bp.route("/prices/import_presets", methods=["POST"])
@admin_required
def import_preset_prices():
    """把预设厂商参考单价导入全局价目表(仅填充缺失项)"""
    imported = 0
    for pr in Preset.query.all():
        for model, price in (pr.prices or {}).items():
            if not db.session.get(ModelPrice, model):
                if isinstance(price, (list, tuple)) and len(price) >= 2:
                    db.session.add(ModelPrice(model=model, input_price=price[0], output_price=price[1]))
                imported += 1
    db.session.commit()
    pricing.refresh_cache()
    return jsonify({"ok": True, "imported": imported})


# ---------- 统计 ----------
@admin_bp.route("/stats/overview", methods=["GET"])
@admin_required
def stats_overview():
    days = request.args.get("days", 1, type=int)
    return jsonify(stats.overview(days=days))


@admin_bp.route("/stats/trend", methods=["GET"])
@admin_required
def stats_trend():
    days = request.args.get("days", 1, type=int)
    return jsonify(stats.hourly_trend(days=days))


@admin_bp.route("/stats/by_model", methods=["GET"])
@admin_required
def stats_model():
    days = request.args.get("days", 30, type=int)
    return jsonify(stats.by_model(days=days, start=request.args.get("start"),
                                  end=request.args.get("end")))


@admin_bp.route("/stats/by_channel", methods=["GET"])
@admin_required
def stats_channel():
    days = request.args.get("days", 30, type=int)
    return jsonify(stats.by_channel(days=days, start=request.args.get("start"),
                                    end=request.args.get("end")))


@admin_bp.route("/stats/by_key", methods=["GET"])
@admin_required
def stats_key():
    days = request.args.get("days", 30, type=int)
    return jsonify(stats.by_key(days=days, start=request.args.get("start"),
                                end=request.args.get("end")))


@admin_bp.route("/stats/logs", methods=["GET"])
@admin_required
def stats_logs():
    limit = request.args.get("limit", 50, type=int)
    success = request.args.get("success")
    only = None
    if success == "true":
        only = True
    elif success == "false":
        only = False
    return jsonify(stats.recent_logs(limit, only))


# ---------- 渠道探测 / 自动获取模型 ----------
@admin_bp.route("/probe/all", methods=["POST"])
@admin_required
def probe_all():
    """手动触发一次全渠道 L1 探测(免费,零 token)"""
    from gateway import probe as probe_mod
    return jsonify(probe_mod.probe_all())


@admin_bp.route("/probe/all/deep", methods=["POST"])
@admin_required
def probe_all_deep():
    """一键深度检查:对所有启用渠道逐一进行 L2 模型探测(后台线程)"""
    from gateway import probe as probe_mod

    def _do():
        app = current_app._get_current_object()
        with app.app_context():
            for ch in Channel.query.filter_by(enabled=True).all():
                try:
                    results = probe_mod.probe_channel_models(ch, max_models=10)
                    ch.model_status = results
                    from gateway import balancer as _bal
                    any_ok = any(r.get("ok") for r in results.values())
                    if any_ok:
                        _bal.note_success(ch)
                    else:
                        _bal.note_failure(ch)
                    db.session.commit()
                except Exception:
                    pass

    threading.Thread(target=_do, daemon=True, name="probe-all-deep").start()
    return jsonify({"ok": True, "message": "已启动全部渠道深度检查,完成后自动刷新查看结果"})


@admin_bp.route("/channels/<int:cid>/probe", methods=["POST"])
@admin_required
def probe_channel_one(cid):
    """单个渠道 L1 探测"""
    from gateway import probe as probe_mod
    ch = db.session.get(Channel, cid)
    if not ch:
        return jsonify({"error": "渠道不存在"}), 404
    return jsonify(probe_mod.probe_one(ch))


@admin_bp.route("/channels/fetch_models", methods=["POST"])
@admin_required
def fetch_models():
    """自动获取模型:用表单里的 base_url/key/proxy/adapter 直连上游模型列表端点。
    支持未保存的临时渠道(新增时用),channel_id 可选。"""
    from gateway import probe as probe_mod
    data = request.get_json(silent=True) or {}

    class _TempChannel:
        pass

    if data.get("channel_id"):
        ch = db.session.get(Channel, int(data["channel_id"]))
        if not ch:
            return jsonify({"error": "渠道不存在"}), 404
        for f in ("base_url", "api_key", "proxy_url", "adapter"):
            val = data.get(f)
            if not val:
                continue
            if f == "api_key" and "***" in val:
                continue   # 掩码值不覆盖已存 Key
            setattr(ch, f, val)
    else:
        ch = _TempChannel()
        ch.base_url = (data.get("base_url") or "").strip()
        ch.api_key = (data.get("api_key") or "").strip()
        ch.proxy_url = (data.get("proxy_url") or "").strip()
        ch.adapter = data.get("adapter") or "openai_compat"
        ch.id = 0
        ch.models, ch.model_mapping, ch.pricing_override = [], {}, {}
        ch.timeout = 0
    if not ch.base_url:
        return jsonify({"error": "base_url 必填"}), 400

    ok, latency, err, models, upstream_meta, source, warning = probe_mod.fetch_models(ch)
    if not ok:
        return jsonify({"ok": False, "error": err or "获取模型失败"}), 200

    # 合并元数据:上游返回优先,内置知识库兜底
    from gateway.model_meta import lookup
    result = []
    for m in models:
        info = dict(upstream_meta.get(m) or {})
        src = "upstream" if info else ""
        if not info:
            kb = lookup(m)
            if kb:
                info, src = kb, "builtin"
        result.append({
            "id": m,
            "context": info.get("context"),
            "max_output": info.get("max_output"),
            "input_price": info.get("input_price"),
            "output_price": info.get("output_price"),
            "currency": info.get("currency", "CNY"),
            "source": src,
        })
    return jsonify({"ok": True, "latency_ms": latency, "models": result,
                    "list_source": source, "warning": warning})


# ---------- 热点统计 ----------
@admin_bp.route("/stats/hot_models", methods=["GET"])
@admin_required
def stats_hot_models():
    days = request.args.get("days", 7, type=int)
    limit = request.args.get("limit", 10, type=int)
    return jsonify(stats.hot_models(days=days, limit=limit))


@admin_bp.route("/stats/daily", methods=["GET"])
@admin_required
def stats_daily():
    """按天统计:每日 tokens / 调用 / 模型数"""
    days = request.args.get("days", 14, type=int)
    daily = stats.daily_usage(days=days)
    model_calls = stats.daily_model_calls(days=days)
    return jsonify({"daily": daily, "model_calls": model_calls})


@admin_bp.route("/stats/hourly_heatmap", methods=["GET"])
@admin_required
def stats_hourly_heatmap():
    days = request.args.get("days", 7, type=int)
    return jsonify(stats.hourly_heatmap(days=days))


@admin_bp.route("/stats/model_hour_heatmap", methods=["GET"])
@admin_required
def stats_model_hour_heatmap():
    days = request.args.get("days", 7, type=int)
    return jsonify(stats.model_hour_heatmap(days=days))


# ---------- 调用日志 ----------
@admin_bp.route("/logs", methods=["GET"])
@admin_required
def list_logs():
    """调用日志:支持模型/渠道/Key/状态/关键词/时间范围筛选与分页"""
    from flask import request as _rq
    from gateway.models import CallLog
    page = max(1, _rq.args.get("page", 1, type=int))
    size = min(200, max(1, _rq.args.get("page_size", 20, type=int)))
    q = CallLog.query
    if _rq.args.get("model"):
        kw = _rq.args["model"].strip()
        q = q.filter(db.or_(CallLog.model_actual.like(f"%{kw}%"),
                            CallLog.model_requested.like(f"%{kw}%")))
    if _rq.args.get("channel_id"):
        q = q.filter(CallLog.channel_id == _rq.args.get("channel_id", type=int))
    if _rq.args.get("key_id"):
        q = q.filter(CallLog.key_id == _rq.args.get("key_id", type=int))
    if _rq.args.get("success") in ("true", "false"):
        q = q.filter(CallLog.success.is_(_rq.args["success"] == "true"))
    if _rq.args.get("stream") in ("true", "false"):
        q = q.filter(CallLog.is_stream.is_(_rq.args["stream"] == "true"))
    if _rq.args.get("request_id"):
        q = q.filter(CallLog.request_id.like(f"%{_rq.args['request_id'].strip()}%"))
    if _rq.args.get("q"):
        kw = f"%{_rq.args['q'].strip()}%"
        q = q.filter(db.or_(CallLog.request_body.like(kw), CallLog.response_body.like(kw),
                            CallLog.error.like(kw), CallLog.client_ip.like(kw),
                            CallLog.key_name.like(kw)))
    if _rq.args.get("start"):
        try:
            from datetime import datetime
            q = q.filter(CallLog.created_at >= datetime.fromisoformat(_rq.args["start"]))
        except ValueError:
            pass
    if _rq.args.get("end"):
        try:
            from datetime import datetime
            q = q.filter(CallLog.created_at <= datetime.fromisoformat(_rq.args["end"]))
        except ValueError:
            pass
    total = q.count()
    rows = q.order_by(CallLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return jsonify({"items": [r.summary() for r in rows], "total": total,
                    "page": page, "page_size": size,
                    "pages": (total + size - 1) // size})


@admin_bp.route("/logs/<int:lid>", methods=["GET"])
@admin_required
def get_log(lid):
    from gateway.models import CallLog
    row = db.session.get(CallLog, lid)
    if not row:
        return jsonify({"error": "日志不存在"}), 404
    return jsonify(row.detail())


@admin_bp.route("/logs", methods=["DELETE"])
@admin_required
def clear_logs():
    """清空调用日志;?days=N 只清 N 天前的"""
    from gateway.models import CallLog
    days = request.args.get("days", type=int)
    if days and days > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        n = CallLog.query.filter(CallLog.created_at < cutoff).delete(synchronize_session=False)
    elif days == 0:
        n = CallLog.query.delete(synchronize_session=False)
    else:
        return jsonify({"error": "需指定 days 参数(0=全部清空)"}), 400
    db.session.commit()
    return jsonify({"ok": True, "deleted": n})


# ---------- 系统设置 ----------
@admin_bp.route("/settings", methods=["GET"])
@admin_required
def get_settings():
    return jsonify({k: Setting.get(k) for k in
                    ("default_timeout", "max_retry", "breaker_threshold",
                     "breaker_cooldown", "probe_interval", "auto_models",
                     "auto_timeout", "auto_max_models",
                     "log_bodies", "log_body_max", "log_retention_days")})


@admin_bp.route("/settings", methods=["POST"])
@admin_required
def set_settings():
    data = request.get_json(silent=True) or {}
    for k in ("default_timeout", "max_retry", "breaker_threshold",
              "breaker_cooldown", "probe_interval", "auto_timeout", "auto_max_models",
              "log_bodies", "log_body_max", "log_retention_days"):
        if k in data:
            try:
                int(data[k])
            except (TypeError, ValueError):
                return jsonify({"error": f"{k} 必须为整数"}), 400
            Setting.set(k, data[k])
    if "auto_models" in data:
        Setting.set("auto_models", data["auto_models"] or "")
    return jsonify({"ok": True})
