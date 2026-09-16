"""转发核心:鉴权模型校验 -> 选路 -> 协议转换 -> 故障转移 -> 计费落库"""
import json
import re
import time
import uuid

import httpx
from flask import Response

from . import balancer, model_degradation, pricing, quota
from .adapters.base import AdapterError, apply_channel_headers
from .adapters.registry import get_adapter
from .adapters.openai_compat import USAGE_SENTINEL
from .db import db
from .models import CallLog, Channel, Setting
from .proxy import get_client

# 可触发故障转移的上游状态码:鉴权失效/限流/超时/服务端错误
FAILOVER_STATUS = {401, 403, 408, 429}

# 上游返回"模型不可识别"的错误关键词(用于 auto 模式快速跳过)
_MODEL_NOT_FOUND_PATTERNS = re.compile(
    r"model[_\s]?not[_\s]?found|does[_\s]?not[_\s]?exist|unknown[_\s]?model|"
    r"no[_\s]?such[_\s]?model|invalid[_\s]?model|model[_\s]?is[_\s]?not[_\s]?supported|"
    r"找不到模型|不存在|未识别|不支持的?模型",
    re.IGNORECASE)


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


def _is_model_not_found(status_code, response_text):
    """检测上游返回的错误是否为"模型不存在/不可识别"
    用于 auto 模式快速跳过,避免在多个渠道间逐个重试同一个不存在的模型"""
    if status_code not in (400, 404):
        return False
    return bool(_MODEL_NOT_FOUND_PATTERNS.search(response_text or ""))


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


def _client_info():
    """从当前请求上下文提取客户端 IP 与 User-Agent(生成器阶段上下文已失效,需提前取)
    IP 优先级:X-Real-IP(反向代理设置的单值头) → X-Forwarded-For 首段 → remote_addr"""
    try:
        from flask import has_request_context, request
        if has_request_context():
            ip = (request.headers.get("X-Real-IP")
                  or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
                  or request.remote_addr or "")
            return ip[:64], (request.headers.get("User-Agent") or "")[:256]
    except Exception:
        pass
    return "", ""


def _log_bodies_enabled():
    try:
        return int(Setting.get("log_bodies", 1)) != 0
    except (TypeError, ValueError):
        return True


def _body_max():
    try:
        return max(0, int(Setting.get("log_body_max", 2000)))
    except (TypeError, ValueError):
        return 2000


def _truncate(text, limit=None):
    if limit is None:
        limit = _body_max()
    if not text or limit <= 0:
        return ""
    return text[:limit] + ("…[已截断]" if len(text) > limit else "")


def write_call_log(request_id, key_row, channel, kind, model_requested, model_actual,
                   status_code, success, latency_ms, retries, pt, ct, cache_read, cost,
                   is_stream, client_ip, user_agent, request_body, response_body, error="",
                   cache_hit=False):
    """写入调用日志(异步队列批量落库)"""
    if not _log_bodies_enabled():
        request_body = response_body = ""
    elif request_body is not None:
        request_body = _truncate(request_body)
        response_body = _truncate(response_body)
    record = dict(
        _log_type="call",
        request_id=request_id,
        key_id=key_row.id if key_row else None,
        key_name=(key_row.name or key_row.key[:8]) if key_row else "",
        channel_id=channel.id if channel else None,
        channel_name=channel.name if channel else "",
        endpoint=kind, model_requested=model_requested or "", model_actual=model_actual or "",
        is_stream=bool(is_stream), status_code=status_code, success=success,
        latency_ms=latency_ms, retries=retries,
        prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct,
        cache_read_tokens=cache_read, cost=cost,
        cache_hit=bool(cache_hit),
        client_ip=client_ip, user_agent=user_agent,
        request_body=request_body or "", response_body=response_body or "",
        error=(error or "")[:500])
    from . import logqueue
    logqueue.enqueue(record)
    return record


def cleanup_call_logs():
    """按保留期清理调用日志(0 = 永久保留)"""
    from datetime import datetime, timedelta, timezone
    try:
        days = int(Setting.get("log_retention_days", 7))
    except (TypeError, ValueError):
        days = 7
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    n = CallLog.query.filter(CallLog.created_at < cutoff).delete(synchronize_session=False)
    db.session.commit()
    return n


def _auto_candidates(api_key_row):
    """model=auto 时的候选模型序列(最多 auto_max_models 个,默认 5):
    1. 设置中的 auto_models 偏好列表(逗号分隔)
    2. Key 白名单
    3. 所有启用渠道的模型(按渠道优先级降序)
    已按 Key 白名单过滤。"""
    try:
        limit = int(Setting.get("auto_max_models", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))
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
    # 排序:正常模型优先,降级模型排后(冷却期内的模型仍可用但优先级降低)
    return model_degradation.sort_candidates(cands[:limit])


def relay_request(app, api_key_row, model, kind, openai_body):
    """主入口。model=auto 时依次尝试候选模型(前一模型无可用渠道则换下一个)。"""
    if model == "auto":
        cands = _auto_candidates(api_key_row)
        if not cands:
            raise RelayError("auto 模式没有可用模型(检查渠道与 auto 偏好设置)", 404)
        # auto 总时间预算(默认 120s):避免多个慢候选依次超时把客户端拖死
        try:
            budget = int(Setting.get("auto_timeout", 120))
        except (TypeError, ValueError):
            budget = 120
        deadline = time.time() + max(15, budget)
        last = None
        for m in cands:
            try:
                # 收紧单候选超时,给后续候选留出时间
                per = max(10, int((deadline - time.time()) / max(1, len(cands))))
                result = _relay_one(app, api_key_row, m, kind, openai_body,
                                   deadline=deadline, per_timeout=per)
                # 成功:清除该模型的降级状态(如有)
                model_degradation.record_model_success(m)
                return result
            except RelayError as e:
                # 404=无渠道或模型不可识别;502=该模型所有渠道尝试失败 —— 均降级到下一候选模型
                if e.status not in (404, 502):
                    raise
                # 记录模型失败,后续请求自动降权
                model_degradation.record_model_failure(m, str(e))
                last = e
                if time.time() >= deadline:
                    break
        raise last or RelayError("auto 模式没有可用模型", 404)
    return _relay_one(app, api_key_row, model, kind, openai_body)


def _relay_one(app, api_key_row, model, kind, openai_body, deadline=None, per_timeout=None):
    stream = bool(openai_body.get("stream"))
    max_retry = quota.get_setting_int("max_retry", 3)
    request_text = _read_request_text(openai_body)
    tried = []
    last_error = None
    started = time.time()
    # 请求上下文信息(生成器阶段会失效,提前捕获)
    request_id = uuid.uuid4().hex[:16]
    client_ip, user_agent = _client_info()
    try:
        request_body_text = json.dumps(openai_body, ensure_ascii=False)
    except (TypeError, ValueError):
        request_body_text = ""
    ctx = {"request_id": request_id, "client_ip": client_ip, "user_agent": user_agent,
           "request_body": request_body_text, "model_requested": model,
           "is_stream": stream}

    candidates = balancer.pick_candidates(model, exclude_ids=set())
    if not candidates:
        # 唯一例外:模型只有已熔断渠道时,强行尝试一次熔断渠道(尽力而为)
        channels = Channel.query.filter_by(enabled=True).all()
        candidates = [c for c in channels if not (c.models and model not in c.models)][:1]
    if not candidates:
        write_call_log(ctx["request_id"], api_key_row, None, kind, model, model,
                       404, False, int((time.time() - started) * 1000), 0,
                       0, 0, 0, 0.0, stream, ctx["client_ip"], ctx["user_agent"],
                       ctx["request_body"], "",
                       error=f"没有可用渠道支持模型 {model}")
        raise RelayError(f"没有可用渠道支持模型 {model}", 404)

    last_channel = None
    prev_tier = None
    _tier_labels = {0: "未分类", 1: "第一梯队", 2: "第二梯队", 3: "第三梯队"}
    for attempt, channel in enumerate(candidates):
        if attempt >= max_retry:
            break
        last_channel = channel

        # 跨梯队降级日志
        cur_tier = balancer.channel_tier(channel)
        if prev_tier is not None and cur_tier != prev_tier and prev_tier > 0:
            app.logger.warning("跨梯队降级: %s -> %s (第%d次尝试, 模型=%s)",
                               _tier_labels.get(prev_tier, f"T{prev_tier}"),
                               channel.tier_label, attempt, model)
        prev_tier = cur_tier

        adapter = get_adapter(channel.adapter)
        upstream_model = channel.real_model(model)
        timeout = _get_timeout(channel)
        if per_timeout:
            timeout = min(timeout, per_timeout)   # auto 场景收紧单渠道超时
        client = get_client(channel, timeout)
        tried.append(channel.id)

        # ---------- 多 key 同渠道重试 ----------
        keys = [k.strip() for k in (channel.api_key or "").split(",") if k.strip()]
        key_count = max(len(keys), 1)
        # 单 key 时保持原有行为;多 key 时逐个尝试
        for key_idx in range(key_count):
            cur_key = keys[key_idx] if keys else channel.current_api_key()
            try:
                req = adapter.build_request(channel, cur_key, upstream_model, kind, openai_body)
                apply_channel_headers(channel, req.headers)
            except AdapterError as e:
                last_error = str(e)
                break  # 构建请求失败,直接换渠道

            try:
                if req.stream:
                    return _do_stream(app, api_key_row, channel, adapter, client, req,
                                      model, kind, request_text, attempt, started, ctx)
                resp = client.post(req.url, headers=req.headers, json=req.json_body,
                                   timeout=timeout)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError) as e:
                last_error = f"网络错误: {type(e).__name__}"
                if key_idx + 1 < key_count:
                    app.logger.warning("渠道 %s key[%d] 网络错误,尝试下一个 key", channel.name, key_idx)
                    continue  # 同渠道换下一个 key
                balancer.note_failure(channel)
                break  # 所有 key 都试过,换渠道
            except RelayError as e:
                last_error = str(e)
                if key_idx + 1 < key_count:
                    app.logger.warning("渠道 %s key[%d] 流错误,尝试下一个 key", channel.name, key_idx)
                    continue
                balancer.note_failure(channel)
                break

            if resp.status_code == 200:
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
                resp_text = json.dumps(openai_json, ensure_ascii=False)
                _finish_success(api_key_row, channel, model, pt, ct, total, cost,
                                int((time.time() - started) * 1000), False, estimated, attempt,
                                cache_read=cache.get("cache_read", 0),
                                cache_creation=cache.get("cache_creation", 0),
                                kind=kind, ctx=ctx, response_text=resp_text)
                return Response(resp_text, status=200, content_type="application/json",
                                headers={"X-Request-Id": request_id})

            last_error = f"上游 {resp.status_code}: {resp.text[:200]}"
            if _failoverable(resp.status_code):
                if key_idx + 1 < key_count:
                    app.logger.warning("渠道 %s key[%d] 返回 %d,尝试下一个 key",
                                       channel.name, key_idx, resp.status_code)
                    continue  # 同渠道换下一个 key
                balancer.note_failure(channel)
                break  # 所有 key 都试过,换渠道
            # 模型不可识别:立即中断
            if _is_model_not_found(resp.status_code, resp.text):
                raise RelayError(f"模型不可识别: {last_error}", 404)
            # 请求本身有问题,直接透传
            _log_failure(api_key_row, channel, model, resp.status_code, last_error,
                         attempt, started, request_text, kind=kind, ctx=ctx,
                         response_text=resp.text[:2000])
            return Response(resp.text, status=resp.status_code,
                            content_type="application/json",
                            headers={"X-Request-Id": request_id})

        # 当前渠道所有 key 都已尝试,继续下一个渠道
        continue

    # 所有候选渠道尝试失败:记录失败日志便于排查
    write_call_log(ctx["request_id"], api_key_row, last_channel, kind, ctx["model_requested"],
                   model, 502, False, int((time.time() - started) * 1000), len(tried),
                   0, 0, 0, 0.0, stream, ctx["client_ip"], ctx["user_agent"],
                   ctx["request_body"], "",
                   error=f"所有渠道尝试失败({len(tried)} 个),最后错误: {last_error}")
    raise RelayError(f"所有渠道尝试失败({len(tried)} 个),最后错误: {last_error}")


def _sse_data_lines(resp):
    """从 httpx 流式响应提取 SSE data 载荷"""
    for line in resp.iter_lines():
        if line.startswith("data:"):
            yield line[5:].strip()


def _do_stream(app, api_key_row, channel, adapter, client, req,
               model, kind, request_text, attempt, started, ctx=None):
    timeout = _get_timeout(channel)
    ctx = ctx or {}

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
        # 模型不可识别:立即中断,由 auto 模式降级到下一候选
        if _is_model_not_found(resp.status_code, body):
            raise RelayError(f"模型不可识别: 上游 {resp.status_code}: {body}", 404)
        _log_failure(api_key_row, channel, model, resp.status_code,
                     f"上游 {resp.status_code}: {body}", attempt, started, request_text,
                     kind=kind, ctx=ctx, response_text=body)
        return Response(body, status=resp.status_code, content_type="application/json",
                        headers={"X-Request-Id": ctx.get("request_id", "")})

    balancer.note_success(channel)
    channel_id, channel_name = channel.id, channel.name
    real_app = app._get_current_object()  # 生成器执行时已脱离请求上下文,需真实对象

    def generate():
        collected_text = []
        collected_chunks = []  # 收集所有 SSE data 用于缓存
        usage = {}
        status_code, error_msg = 200, ""
        completed = False  # 上游流完整结束且已全部转发;客户端断开时保持 False,禁止缓存截断响应
        with real_app.app_context():
            try:
                for item in adapter.transform_stream(_sse_data_lines(resp)):
                    if isinstance(item, tuple) and item[0] == USAGE_SENTINEL:
                        usage = item[1]
                        continue
                    data = item
                    collected_chunks.append(data)
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
                completed = True
            except (httpx.ReadError, httpx.RemoteProtocolError) as e:
                status_code, error_msg = 502, f"流中断: {type(e).__name__}"
                yield f"data: {json.dumps(_error_body(error_msg), ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except GeneratorExit:
                # 客户端提前断开:不缓存不完整流,记失败便于排查;继续向上传播
                status_code, error_msg = 499, "客户端中断"
                raise
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
                pt, ct, estimated = quota.calc_and_get_usage(
                    model, None, usage, request_text, "".join(collected_text))
                cost = pricing.calc_cost(model, pt, ct, channel)
                latency = int((time.time() - started) * 1000)
                try:
                    if status_code == 200 and completed:
                        _finish_success(api_key_row, channel, model, pt, ct, pt + ct, cost,
                                        latency, True, estimated, attempt,
                                        cache_read=usage.get("cache_read", 0),
                                        cache_creation=usage.get("cache_creation", 0),
                                        kind=kind, ctx=ctx,
                                        response_text="".join(collected_text))
                        # 流式写入缓存(仅在流完整结束时写入,避免缓存截断响应,按渠道隔离)
                    else:
                        _log_failure(api_key_row, channel, model, status_code, error_msg,
                                     attempt, started, request_text, kind=kind, ctx=ctx,
                                     response_text="".join(collected_text))
                except Exception:
                    # 日志/缓存落库失败不应干扰流结束或 GeneratorExit 传播
                    pass

    return Response(generate(), status=200,
                    content_type="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _finish_success(api_key_row, channel, model, pt, ct, total, cost,
                    latency_ms, is_stream, estimated, attempt,
                    cache_read=0, cache_creation=0, kind="", ctx=None, response_text="",
                    cache_hit=False):
    if total > 0:
        quota.consume(api_key_row, total)
    if api_key_row:
        quota.log_usage(
            key_id=api_key_row.id, key_name=api_key_row.name or api_key_row.key[:8],
            channel_id=channel.id if channel else None, channel_name=channel.name if channel else "",
            model=model, prompt_tokens=pt, completion_tokens=ct, total_tokens=total, cost=cost,
            cache_read_tokens=cache_read, cache_creation_tokens=cache_creation,
            cache_hit=cache_hit,
            latency_ms=latency_ms, status_code=200, success=True, is_stream=is_stream,
            estimated=estimated, retries=attempt, error="")
    ctx = ctx or {}
    write_call_log(ctx.get("request_id", ""), api_key_row, channel, kind,
                   ctx.get("model_requested", model), model, 200, True, latency_ms,
                   attempt, pt, ct, cache_read, cost, is_stream,
                   ctx.get("client_ip", ""), ctx.get("user_agent", ""),
                   ctx.get("request_body", ""), response_text, cache_hit=cache_hit)


def _log_failure(api_key_row, channel, model, status_code, error, attempt,
                 started, request_text, kind="", ctx=None, response_text=""):
    ctx = ctx or {}
    latency_ms = int((time.time() - started) * 1000)
    quota.log_usage(
        key_id=api_key_row.id, key_name=api_key_row.name or api_key_row.key[:8],
        channel_id=channel.id, channel_name=channel.name, model=model,
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cost=0.0,
        latency_ms=latency_ms, status_code=status_code,
        success=False, is_stream=False, estimated=False, retries=attempt,
        error=(error or "")[:500])
    write_call_log(ctx.get("request_id", ""), api_key_row, channel, kind,
                   ctx.get("model_requested", model), model, status_code, False,
                   latency_ms, attempt, 0, 0, 0, 0.0,
                   ctx.get("is_stream", False),
                   ctx.get("client_ip", ""), ctx.get("user_agent", ""),
                   ctx.get("request_body", ""), response_text, error=error)
