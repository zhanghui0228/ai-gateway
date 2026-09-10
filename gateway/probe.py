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
from .adapters.registry import get_adapter
from .db import db
from .models import Channel, Setting
from .proxy import get_client


def probe_channel(channel):
    """L1 探测。返回 (ok, latency_ms, error, models, models_meta)
    models_meta: {model_id: {context/max_output/input_price/output_price/currency}}"""
    adapter = get_adapter(channel.adapter)
    api_key = (channel.current_api_key() if hasattr(channel, "current_api_key")
               else (channel.api_key or "").split(",")[0].strip())
    try:
        url, headers = adapter.models_request(channel, api_key)
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
            return False, latency, f"HTTP {resp.status_code}: {resp.text[:120]}", [], {}
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


def probe_one(channel):
    """探测单个已保存渠道并落库,返回结果 dict"""
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
    threading.Thread(target=run, daemon=True, name="probe-scheduler").start()
