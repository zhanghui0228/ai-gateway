"""转发核心:鉴权模型校验 -> 选路 -> 协议转换 -> 故障转移 -> 计费落库"""
import json
import time

import httpx
from flask import Response

from . import balancer, pricing, quota
from .adapters.base import AdapterError
from .adapters.registry import get_adapter
from .adapters.openai_compat import USAGE_SENTINEL
from .db import db
from .models import Channel, Setting
from .proxy import get_client

# 可触发故障转移的上游状态码:鉴权失效/限流/超时/服务端错误
FAILOVER_STATUS = {401, 403, 408, 429}


class RelayError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def _failoverable(status_code):
    return status_code in FAILOVER_STATUS or status_code >= 500


def _get_timeout(channel):
    if channel.timeout and channel.timeout > 0:
        return channel.timeout
    try:
        return int(Setting.get("default_timeout", 120))
    except (TypeError, ValueError):
        return 120


def _error_body(message, err_type="upstream_error", code=None):
    return {"error": {"message": message, "type": err_type, "code": code}}


def _read_request_text(openai_body):
    """粗略提取请求文本用于 usage 估算兜底"""
    msgs = openai_body.get("messages") or []
    total = []
    for m in msgs:
        c = m.get("content")
        if isinstance(c, str):
            total.append(c)
        elif c:
            total.extend(p.get("text", "") for p in c if isinstance(p, dict))
    if openai_body.get("prompt"):
        p = openai_body["prompt"]
        total.append(p if isinstance(p, str) else " ".join(map(str, p)))
    return " ".join(total)


def _auto_candidates(api_key_row):
    """model=auto 时的候选模型序列:
    1. 设置中的 auto_models 偏好列表(逗号分隔)
    2. Key 白名单
    3. 所有启用渠道的模型(按渠道优先级降序)
    已按 Key 白名单过滤。"""
    prefs = [m.strip() for m in (Setting.get("auto_models", "") or "").split(",") if m.strip()]
    allowed = api_key_row.allowed_models or []
    if prefs:
        cands = [m for m in prefs if not allowed or m in allowed]
    elif allowed:
        cands = list(allowed)
    else:
        cands = []
        for ch in Channel.query.filter_by(enabled=True).order_by(Channel.priority.desc()).all():
            for m in (ch.models or []):
                if m not in cands:
                    cands.append(m)
    return cands


def relay_request(app, api_key_row, model, kind, openai_body):
    """主入口。model=auto 时依次尝试候选模型(前一模型无可用渠道则换下一个)。"""
    if model == "auto":
        cands = _auto_candidates(api_key_row)
        if not cands:
            raise RelayError("auto 模式没有可用模型(检查渠道与 auto 偏好设置)", 404)
        last = None
        for m in cands:
            try:
                return _relay_one(app, api_key_row, m, kind, openai_body)
            except RelayError as e:
                # 404=无渠道;502=该模型所有渠道尝试失败 —— 均降级到下一候选模型
                if e.status not in (404, 502):
                    raise
                last = e
        raise last or RelayError("auto 模式没有可用模型", 404)
    return _relay_one(app, api_key_row, model, kind, openai_body)


def _relay_one(app, api_key_row, model, kind, openai_body):
    stream = bool(openai_body.get("stream"))
    max_retry = quota.get_setting_int("max_retry", 3)
    request_text = _read_request_text(openai_body)
    tried = []
    last_error = None
    started = time.time()

    candidates = balancer.pick_candidates(model, exclude_ids=set())
    if not candidates:
        # 唯一例外:模型只有已熔断渠道时,强行尝试一次熔断渠道(尽力而为)
        channels = Channel.query.filter_by(enabled=True).all()
        candidates = [c for c in channels if not (c.models and model not in c.models)][:1]
    if not candidates:
        raise RelayError(f"没有可用渠道支持模型 {model}", 404)

    for attempt, channel in enumerate(candidates):
        if attempt >= max_retry:
            break
        adapter = get_adapter(channel.adapter)
        upstream_model = channel.real_model(model)
        timeout = _get_timeout(channel)
        client = get_client(channel, timeout)
        tried.append(channel.id)

        try:
            req = adapter.build_request(channel, channel.current_api_key(),
                                        upstream_model, kind, openai_body)
        except AdapterError as e:
            last_error = str(e)
            continue

        try:
            if req.stream:
                return _do_stream(app, api_key_row, channel, adapter, client, req,
                                  model, kind, request_text, attempt, started)
            resp = client.post(req.url, headers=req.headers, json=req.json_body,
                               timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
            last_error = f"网络错误: {type(e).__name__}"
            balancer.note_failure(channel)
            continue
        except RelayError as e:
            # _do_stream 在拿到响应头之前失败(网络/可故障转移状态码),换下一渠道
            last_error = str(e)
            balancer.note_failure(channel)
            continue

        if resp.status_code != 200:
            last_error = f"上游 {resp.status_code}: {resp.text[:200]}"
            if _failoverable(resp.status_code):
                balancer.note_failure(channel)
                continue
            # 请求本身有问题(400/404/422),直接透传给客户端
            _log_failure(api_key_row, channel, model, resp.status_code, last_error,
                         attempt, started, request_text)
            return Response(resp.text, status=resp.status_code,
                            content_type="application/json")

        # 成功
        balancer.note_success(channel)
        try:
            up_json = resp.json()
        except ValueError:
            up_json = {}
        openai_json = adapter.adapt_response(up_json, kind)
        usage = adapter.extract_usage(up_json) if kind != "images" else {}
        cache = adapter.extract_token_details(up_json) if kind == "chat" else {}
        pt, ct, estimated = quota.calc_and_get_usage(model, channel, usage,
                                                     request_text, "")
        if not (pt or ct):
            pt, ct, estimated = quota.calc_and_get_usage(model, channel, {}, request_text, "")
        cost = pricing.calc_cost(model, pt, ct, channel)
        total = pt + ct
        _finish_success(api_key_row, channel, model, pt, ct, total, cost,
                        int((time.time() - started) * 1000), False, estimated, attempt,
                        cache_read=cache.get("cache_read", 0),
                        cache_creation=cache.get("cache_creation", 0))
        return Response(json.dumps(openai_json, ensure_ascii=False),
                        status=200, content_type="application/json")

    raise RelayError(f"所有渠道尝试失败({len(tried)} 个),最后错误: {last_error}")


def _sse_data_lines(resp):
    """从 httpx 流式响应提取 SSE data 载荷"""
    for line in resp.iter_lines():
        if line.startswith("data:"):
            yield line[5:].strip()


def _do_stream(app, api_key_row, channel, adapter, client, req,
               model, kind, request_text, attempt, started):
    timeout = _get_timeout(channel)

    # 先发起请求确认能拿到 200,再交给生成器(此阶段失败仍可故障转移)
    try:
        resp = client.send(client.build_request("POST", req.url, headers=req.headers,
                                                json=req.json_body),
                           stream=True)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
        raise RelayError(f"网络错误: {type(e).__name__}")

    if resp.status_code != 200:
        body = resp.read().decode("utf-8", "replace")[:300]
        resp.close()
        if _failoverable(resp.status_code):
            raise RelayError(f"上游 {resp.status_code}: {body}")
        _log_failure(api_key_row, channel, model, resp.status_code,
                     f"上游 {resp.status_code}: {body}", attempt, started, request_text)
        return Response(body, status=resp.status_code, content_type="application/json")

    balancer.note_success(channel)
    channel_id, channel_name = channel.id, channel.name
    real_app = app._get_current_object()  # 生成器执行时已脱离请求上下文,需真实对象

    def generate():
        collected_text = []
        usage = {}
        status_code, error_msg = 200, ""
        with real_app.app_context():
            try:
                for item in adapter.transform_stream(_sse_data_lines(resp)):
                    if isinstance(item, tuple) and item[0] == USAGE_SENTINEL:
                        usage = item[1]
                        continue
                    data = item
                    # 顺带收集文本用于估算兜底
                    try:
                        j = json.loads(data)
                        if isinstance(j, dict):
                            d = (j.get("choices") or [{}])[0].get("delta") or {}
                            if d.get("content"):
                                collected_text.append(d["content"])
                    except (ValueError, TypeError, IndexError, KeyError):
                        pass
                    yield f"data: {data}\n\n"
            except (httpx.ReadError, httpx.RemoteProtocolError) as e:
                status_code, error_msg = 502, f"流中断: {type(e).__name__}"
                yield f"data: {json.dumps(_error_body(error_msg), ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
                pt, ct, estimated = quota.calc_and_get_usage(
                    model, None, usage, request_text, "".join(collected_text))
                cost = pricing.calc_cost(model, pt, ct, channel)
                latency = int((time.time() - started) * 1000)
                if status_code == 200:
                    _finish_success(api_key_row, channel, model, pt, ct, pt + ct, cost,
                                    latency, True, estimated, attempt,
                                    cache_read=usage.get("cache_read", 0),
                                    cache_creation=usage.get("cache_creation", 0))
                else:
                    _log_failure(api_key_row, channel, model, status_code, error_msg,
                                 attempt, started, request_text)

    return Response(generate(), status=200,
                    content_type="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _finish_success(api_key_row, channel, model, pt, ct, total, cost,
                    latency_ms, is_stream, estimated, attempt,
                    cache_read=0, cache_creation=0):
    if total > 0:
        quota.consume(api_key_row, total)
    quota.log_usage(
        key_id=api_key_row.id, key_name=api_key_row.name or api_key_row.key[:8],
        channel_id=channel.id, channel_name=channel.name, model=model,
        prompt_tokens=pt, completion_tokens=ct, total_tokens=total, cost=cost,
        cache_read_tokens=cache_read, cache_creation_tokens=cache_creation,
        latency_ms=latency_ms, status_code=200, success=True, is_stream=is_stream,
        estimated=estimated, retries=attempt, error="")


def _log_failure(api_key_row, channel, model, status_code, error, attempt,
                 started, request_text):
    quota.log_usage(
        key_id=api_key_row.id, key_name=api_key_row.name or api_key_row.key[:8],
        channel_id=channel.id, channel_name=channel.name, model=model,
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=0.0,
        latency_ms=int((time.time() - started) * 1000), status_code=status_code,
        success=False, is_stream=False, estimated=False, retries=attempt,
        error=(error or "")[:500])
