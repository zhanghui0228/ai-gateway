"""渠道专属 HTTP 客户端工厂:每渠道独立代理,无全局代理也可访问外网"""
import threading

import httpx

_lock = threading.Lock()
_clients = {}  # (channel_id, proxy_url, timeout) -> httpx.Client


def get_client(channel, timeout):
    """获取/复用渠道专属 httpx Client。socks5 代理由 httpx[socks] 支持。"""
    proxy_url = (channel.proxy_url or "").strip()
    cache_key = (channel.id, proxy_url, timeout)
    with _lock:
        client = _clients.get(cache_key)
        if client is not None and not client.is_closed:
            return client
        proxies = proxy_url if proxy_url else None
        client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=15),
            proxy=proxies,
            trust_env=False,   # 忽略系统环境变量代理,完全以渠道配置为准
            follow_redirects=True,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        _clients[cache_key] = client
        return client


def drop_channel(channel_id):
    """渠道代理配置变更后清理旧 Client"""
    with _lock:
        for key in [k for k in _clients if k[0] == channel_id]:
            client = _clients.pop(key)
            try:
                client.close()
            except Exception:
                pass
