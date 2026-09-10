"""实时事件总线:用量落库后推送给大屏 SSE 订阅者"""
import json
import queue
import threading

_lock = threading.Lock()
_subscribers = []  # list[queue.Queue]


def subscribe():
    q = queue.Queue(maxsize=100)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def publish(event_type, data):
    """event_type: usage / channel_status"""
    msg = json.dumps({"type": event_type, "data": data}, ensure_ascii=False)
    with _lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass
