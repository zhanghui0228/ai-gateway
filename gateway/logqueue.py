"""异步批量日志写入:CallLog 和 UsageLog 通过队列批量落库,减少 SQLite 写入竞争"""
import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_queue: deque[dict] = deque()
_flush_event = threading.Event()
_running = False
_thread: threading.Thread | None = None
_app = None  # Flask app 实例,由 start(app) 传入

# 批量配置
MAX_BATCH_SIZE = 50      # 单次最多写入条数
FLUSH_INTERVAL = 2.0     # 强制刷盘间隔(秒)
MAX_QUEUE_SIZE = 5000    # 队列上限,超出时丢弃最旧记录


def start(app=None):
    """启动后台刷盘线程
    
    Args:
        app: Flask app 实例,建议在 create_app 中传入以确保 app context 可用
    """
    global _running, _thread, _app
    with _lock:
        if _running:
            return
        _app = app
        _running = True
        _thread = threading.Thread(target=_flush_loop, daemon=True, name="log-writer")
        _thread.start()


def stop():
    """停止后台线程并刷盘剩余数据"""
    global _running
    with _lock:
        _running = False
    _flush_event.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)
    _force_flush()


def enqueue(record: dict):
    """将日志记录放入写入队列"""
    with _lock:
        if len(_queue) >= MAX_QUEUE_SIZE:
            # 队列满了,丢弃最旧的一条
            _queue.popleft()
        _queue.append(record)


def _flush_loop():
    """后台刷盘循环"""
    while _running:
        _flush_event.wait(timeout=FLUSH_INTERVAL)
        _flush_event.clear()
        _force_flush()


def _force_flush():
    """将队列中的数据批量写入数据库"""
    from .db import db
    from .models import CallLog, UsageLog

    # 获取 app 实例:优先使用 _app,否则回退到 db.app
    app = _app
    if app is None:
        try:
            app = db.app
        except Exception:
            pass
    if app is None:
        logger.error("日志批量写入失败: 无可用 Flask app 实例")
        return

    while True:
        with _lock:
            if not _queue:
                return
            batch = []
            for _ in range(min(MAX_BATCH_SIZE, len(_queue))):
                batch.append(_queue.popleft())

        try:
            with app.app_context():
                for record in batch:
                    try:
                        log_type = record.pop("_log_type", "call")
                        if log_type == "usage":
                            db.session.add(UsageLog(**record))
                        else:
                            db.session.add(CallLog(**record))
                    except Exception as e:
                        logger.error("日志记录构造失败: %s", e)
                db.session.commit()
        except Exception as e:
            logger.error("日志批量写入失败: %s", e, exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass


def queue_size() -> int:
    """返回当前队列中的待写入条数"""
    with _lock:
        return len(_queue)
