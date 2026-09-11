"""渠道健康探测

两级探测策略,避免消耗模型额度:
- L1(定时/自动): GET 上游模型列表端点 —— 零 token 消耗,验证连通性/鉴权/代理,
  顺带获取模型清单(供"自动获取模型"功能复用)
- L2(手动"测试"按钮): max_tokens=1 真实补全 —— 会消耗极少 token,仅管理台手动触发
"""
import threading
import time
from datetime import datetime, timezone

import httpx

from . import events
from .adapters.base import apply_channel_headers
from .adapters.registry import get_adapter
from .db import db
from .models import Channel, Setting
from .proxy import get_client


def _auth_hint(status_code, msg):
    """把上游鉴权失败翻译成可操作的提示"""
    text = str(msg or "")
    if status_code == 401 or "unauthorized" in text.lower() or "invalid" in text.lower():
        return ("上游拒绝鉴权(401):该 Key 未被站点接受。请在站点后台重新生成 API 令牌"
                "(通常 sk- 开头,注意区分账号 access token 与 API 令牌),确认令牌已启用且分组包含目标模型")
    if status_code == 402:
        return "上游额度/预算已耗尽(402),请到站点后台查看额度或等待额度发放"
    if status_code == 403:
        return "上游拒绝访问(403):令牌分组无此模型权限或被风控拦截"
    if status_code == 429:
        return "上游限流(429),稍后重试"
    return ""


def probe_chat_endpoint(channel):
    """L2 聊天端点探测:max_tokens=1 真实补全(消耗极少 token)。
    用于上游禁用了模型列表端点的站点(如 AgentRouter 公益站)。"""
    adapter = get_adapter(channel.adapter)
    models = channel.models or []
    if not models:
        return False, 0, "渠道未配置模型,无法聊天探测", []
    upstream_model = channel.real_model(models[0])
    try:
        req = adapter.build_request(channel, channel.current_api_key(), upstream_model, "chat",
                                    {"model": upstream_model,
                                     "messages": [{"role": "user", "content": "hi"}],
                                     "max_tokens": 1, "stream": False})
        apply_channel_headers(channel, req.headers)
    except Exception as e:
        return False, 0, str(e), []

    client = get_client(channel, min(channel.timeout or 30, 30))
    t0 = time.time()
    try:
        resp = client.post(req.url, headers=req.headers, json=req.json_body)
        latency = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            return True, latency, "", []
        try:
            detail = resp.json()
            msg = (detail.get("error") or {}).get("message") or detail.get("message") or resp.text
        except ValueError:
            msg = resp.text
        hint = _auth_hint(resp.status_code, msg)
        err = f"HTTP {resp.status_code}: {str(msg)[:120]}"
        if hint:
            err += f" | {hint}"
        return False, latency, err, []
    except httpx.HTTPError as e:
        return False, int((time.time() - t0) * 1000), type(e).__name__, []
    except Exception as e:
        return False, 0, str(e)[:120], []


def probe_channel(channel):
    """L1 探测。返回 (ok, latency_ms, error, models, models_meta)
    models_meta: {model_id: {context/max_output/input_price/output_price/currency}}"""
    adapter = get_adapter(channel.adapter)
    api_key = (channel.current_api_key() if hasattr(channel, "current_api_key")
               else (channel.api_key or "").split(",")[0].strip())
    try:
        url, headers = adapter.models_request(channel, api_key)
        apply_channel_headers(channel, headers)
    except Exception as e:
        return False, 0, str(e), [], {}

    temp = not getattr(channel, "id", 0)   # 未保存的临时渠道(表单直连测试)
    client = (httpx.Client(timeout=15, proxy=channel.proxy_url or None, trust_env=False)
              if temp else get_client(channel, 15))
    t0 = time.time()
    try:
        resp = client.get(url, headers=headers, timeout=15)
        latency = int((time.time() - t0) * 1000)
        if resp.status_code != 200:
            hint = _auth_hint(resp.status_code, resp.text[:200])
            err = f"HTTP {resp.status_code}: {resp.text[:120]}"
            if hint:
                err += f" | {hint}"
            return False, latency, err, [], {}
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return True, latency, "", adapter.parse_models(data), adapter.parse_models_meta(data)
    except httpx.HTTPError as e:
        return False, int((time.time() - t0) * 1000), type(e).__name__, [], {}
    finally:
        if temp:
            client.close()


def _save_probe(channel, ok, latency, err):
    channel.probe_ok = ok
    channel.probe_latency = latency
    channel.probe_at = datetime.now(timezone.utc).isoformat()
    channel.probe_error = (err or "")[:200]
    db.session.commit()


def fetch_models(channel):
    """获取模型清单(供"自动获取模型")。
    优先请求上游 /v1/models;若该端点被禁用(部分公益站只放行 chat/messages),
    回退到公开定价接口(new-api / one-api 系)。
    返回 (ok, latency_ms, error, models, meta, source, warning)
    source: "api" | "pricing" | ""
    """
    ok, latency, err, models, meta = probe_channel(channel)
    if ok and models:
        return ok, latency, "", models, meta, "api", ""

    adapter = get_adapter(channel.adapter)
    temp = not getattr(channel, "id", 0)
    for url, headers in adapter.models_fallback_request(channel):
        client = (httpx.Client(timeout=15, proxy=channel.proxy_url or None, trust_env=False)
                  if temp else get_client(channel, 15))
        try:
            resp = client.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            items = adapter.parse_fallback_models(resp.json())
            if items:
                warn = (f"上游 /v1/models 不可用({err or 'HTTP错误'}),"
                        f"模型清单取自公开定价接口 {url}(未校验 API Key)")
                return True, latency, "", items, {}, "pricing", warn
        except (httpx.HTTPError, ValueError):
            continue
        finally:
            if temp:
                client.close()
    return False, latency, err or "获取模型失败", [], {}, "", ""


def probe_channel_models(channel, max_models=10):
    """L2 多模型逐一探测:对渠道绑定的每个模型发一个 max_tokens=1 的 chat 请求。
    返回 {model_name: {"ok", "status", "latency_ms", "error", "tested_at"}}
    最多测前 max_models 个,避免渠道模型过多时耗时过长。"""
    adapter = get_adapter(channel.adapter)
    models = channel.models or []
    if not models:
        return {}
    results = {}
    from datetime import datetime, timezone
    timeout_cap = min(channel.timeout or 30, 30)
    for m in models[:max_models]:
        upstream_model = channel.real_model(m)
        tested_at = datetime.now(timezone.utc).isoformat()
        try:
            req = adapter.build_request(channel, channel.current_api_key(), upstream_model, "chat",
                                        {"model": upstream_model,
                                         "messages": [{"role": "user", "content": "hi"}],
                                         "max_tokens": 1, "stream": False})
            apply_channel_headers(channel, req.headers)
        except Exception as e:
            results[m] = {"ok": False, "status": 0, "latency_ms": 0,
                           "error": f"构建请求失败: {str(e)[:120]}", "tested_at": tested_at}
            continue

        client = get_client(channel, timeout_cap)
        t0 = time.time()
        try:
            resp = client.post(req.url, headers=req.headers, json=req.json_body,
                               timeout=timeout_cap)
            latency = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                results[m] = {"ok": True, "status": 200, "latency_ms": latency,
                              "error": "", "tested_at": tested_at}
            else:
                try:
                    detail = resp.json()
                    msg = ((detail.get("error") or {}).get("message")
                           or detail.get("message") or resp.text)
                except ValueError:
                    msg = resp.text
                hint = _auth_hint(resp.status_code, msg)
                err = f"HTTP {resp.status_code}: {str(msg)[:120]}"
                if hint:
                    err += f" | {hint}"
                results[m] = {"ok": False, "status": resp.status_code,
                              "latency_ms": latency, "error": err, "tested_at": tested_at}
        except httpx.HTTPError as e:
            results[m] = {"ok": False, "status": 0,
                           "latency_ms": int((time.time() - t0) * 1000),
                           "error": f"{type(e).__name__}", "tested_at": tested_at}
        except Exception as e:
            results[m] = {"ok": False, "status": 0, "latency_ms": 0,
                           "error": str(e)[:120], "tested_at": tested_at}
    return results


def probe_one(channel):
    """探测单个已保存渠道并落库,返回结果 dict。
    按渠道 probe_mode 选择探测方式:models(免费) / chat(极少消耗) / off(跳过)"""
    mode = channel.probe_mode or "models"
    if mode == "off":
        return {"id": channel.id, "name": channel.name, "ok": None,
                "latency_ms": 0, "error": "已关闭探测", "models": [], "skipped": True}
    if mode == "chat":
        ok, latency, err, models = probe_chat_endpoint(channel)
    else:
        ok, latency, err, models, _meta = probe_channel(channel)
    _save_probe(channel, ok, latency, err)
    return {"id": channel.id, "name": channel.name, "ok": ok,
            "latency_ms": latency, "error": channel.probe_error, "models": models}


def probe_all():
    """探测所有启用渠道,推送大屏事件"""
    results = [probe_one(ch) for ch in Channel.query.filter_by(enabled=True).all()]
    if results:
        events.publish("probe", [{k: v for k, v in r.items() if k != "models"} for r in results])
    return results


def _interval():
    try:
        return int(Setting.get("probe_interval", 300))
    except (TypeError, ValueError):
        return 300


_cleanup_callbacks = []


def register_cleanup(fn):
    """注册额外的周期清理回调(在探测调度器每次执行后调用)"""
    _cleanup_callbacks.append(fn)


def start_scheduler(app):
    """后台定时探测线程"""
    real_app = app._get_current_object() if hasattr(app, "_get_current_object") else app

    def read_interval():
        with real_app.app_context():
            return _interval()

    def run():
        while True:
            interval = read_interval()
            time.sleep(60 if interval <= 0 else max(30, interval))
            if read_interval() <= 0:    # 已关闭
                continue
            with real_app.app_context():
                try:
                    probe_all()
                except Exception:
                    pass
                try:
                    from .relay import cleanup_call_logs
                    cleanup_call_logs()
                except Exception:
                    pass
                # 执行注册的额外清理回调
                for fn in _cleanup_callbacks:
                    try:
                        fn()
                    except Exception:
                        pass
    threading.Thread(target=run, daemon=True, name="probe-scheduler").start()
