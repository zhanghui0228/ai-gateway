"""对外统一 OpenAI 兼容 API"""
import json

from flask import Blueprint, jsonify, request

from gateway import quota, relay
from gateway.auth import api_key_required
from gateway.models import Channel, ModelPrice

v1_bp = Blueprint("v1", __name__, url_prefix="/v1")


@v1_bp.route("/models", methods=["GET"])
@api_key_required
def list_models():
    api_key = request.gw_api_key
    models = {}
    for ch in Channel.query.filter_by(enabled=True).all():
        for m in (ch.models or []):
            models.setdefault(m, True)
    allowed = api_key.allowed_models or []
    data = [{"id": m, "object": "model", "owned_by": "gateway",
             "permission": [], "root": m, "parent": None}
            for m in sorted(models.keys()) if not allowed or m in allowed]
    data.insert(0, {"id": "auto", "object": "model", "owned_by": "gateway",
                    "permission": [], "root": "auto", "parent": None})
    return jsonify({"object": "list", "data": data})


@v1_bp.route("/chat/completions", methods=["POST"])
@api_key_required
def chat_completions():
    return _dispatch("chat")


@v1_bp.route("/completions", methods=["POST"])
@api_key_required
def completions():
    return _dispatch("completions")


@v1_bp.route("/embeddings", methods=["POST"])
@api_key_required
def embeddings():
    return _dispatch("embeddings")


@v1_bp.route("/images/generations", methods=["POST"])
@api_key_required
def images_generations():
    return _dispatch("images")


def _dispatch(kind):
    api_key = request.gw_api_key
    body = request.get_json(silent=True) or {}
    model = body.get("model")
    if not model:
        return jsonify({"error": {"message": "缺少 model 参数(可填 auto 由网关自动选择)",
                                  "type": "invalid_request_error"}}), 400
    # auto 模式交给 relay 内部路由;显式模型才校验白名单
    if model != "auto" and not api_key.model_allowed(model):
        return jsonify({"error": {"message": f"API Key 无权访问模型 {model}",
                                  "type": "permission_error"}}), 403
    try:
        from flask import current_app
        resp = relay.relay_request(current_app, api_key, model, kind, body)
        return resp
    except relay.RelayError as e:
        return jsonify({"error": {"message": str(e), "type": "upstream_error",
                                  "code": e.status}}), e.status
