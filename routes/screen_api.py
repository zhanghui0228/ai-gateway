"""大屏 SSE 实时事件流"""
import json
import queue

from flask import Blueprint, Response, current_app, request, session, stream_with_context

from gateway import events, stats
from gateway.auth import admin_required

screen_bp = Blueprint("screen", __name__)


@screen_bp.route("/admin/api/events")
@admin_required
def sse_events():
    q = events.subscribe()

    def stream():
        try:
            yield "data: " + json.dumps({"type": "hello"}) + "\n\n"
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            events.unsubscribe(q)

    return Response(stream_with_context(stream()),
                    content_type="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
