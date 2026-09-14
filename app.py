"""AIGateway 入口"""
import os
import threading
import time

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session
from sqlalchemy import inspect, text

import config
from gateway.auth import init_admin, admin_required, default_settings, fix_settings, fix_settings
from gateway.db import db
from gateway import pricing
from gateway.models import Channel


def _migrate_columns(app):
    """SQLite 轻量迁移:为已存在的表补新增列"""
    with app.app_context():
        insp = inspect(db.engine)
        migrations = {
            "channels": [("probe_ok", "BOOLEAN"), ("probe_at", "TEXT"),
                         ("probe_latency", "INTEGER"), ("probe_error", "TEXT"),
                         ("probe_mode", "TEXT"), ("user_agent", "TEXT"),
                         ("extra_headers", "TEXT"), ("custom_fields", "TEXT"),
                         ("model_status", "TEXT")],
            "model_prices": [("context_window", "INTEGER"), ("max_output", "INTEGER")],
            "usage_logs": [("cache_read_tokens", "BIGINT"), ("cache_creation_tokens", "BIGINT")],
            "call_logs": [("cache_hit", "BOOLEAN")],
            "response_cache": [("chunks", "TEXT")],
        }
        with db.engine.begin() as conn:
            for table, adds in migrations.items():
                if table not in insp.get_table_names():
                    continue
                cols = {c["name"] for c in insp.get_columns(table)}
                for name, typ in adds:
                    if name not in cols:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {typ}"))


def create_app():
    app = Flask(__name__, static_folder="web/static", template_folder="web/templates")
    app.config.from_object(config)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7)

    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrate_columns(app)
        # SQLite WAL 并发友好
        with db.engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.commit()
        pricing.refresh_cache()
        # 从 SQLite 加载未过期缓存到内存 LRU
        from gateway import cache as cache_mod
        n = cache_mod.cache.warm_up()
        if n:
            print(f"  响应缓存已加载 {n} 条热缓存")
    init_admin(app)
    with app.app_context():
        default_settings()
        fix_settings()
        from gateway.presets import seed_presets
        seed_presets()

    # 渠道定时健康探测(L1 免费模型列表探测)
    from gateway import probe
    probe.start_scheduler(app)
    # 注册缓存过期清理(随探测调度器周期执行)
    probe.register_cleanup(cache_mod.cache.cleanup_expired)

    # 版本更新:启动后静默检查一次,失败不影响启动(仅用于尽早给出提醒)
    from gateway import updater as updater_mod

    def _bg_update_check():
        def _do():
            time.sleep(5)
            try:
                with app.app_context():
                    updater_mod.check_update(updater_mod.get_settings())
            except Exception:
                pass

        threading.Thread(target=_do, daemon=True, name="update-check-startup").start()

    _bg_update_check()

    from routes.admin_api import admin_bp
    from routes.screen_api import screen_bp
    from routes.v1_api import v1_bp
    app.register_blueprint(v1_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(screen_bp)

    # ---------- 页面 ----------
    @app.route("/")
    def index():
        if not session.get("admin_id"):
            return render_template("login.html")
        return render_template("console.html")

    @app.route("/screen")
    def screen():
        # 大屏免登录(本机部署场景);可加 ?key= 校验
        return render_template("screen.html")

    @app.route("/docs")
    def api_docs():
        # 对外接口信息页(免登录,仅展示接口说明)
        return render_template("docs.html")

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": {"message": "not found", "type": "not_found"}}), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify({"error": {"message": "internal error", "type": "server_error"}}), 500

    return app


app = create_app()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AIGateway 本地模型网关")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5100)
    parser.add_argument("--dev", action="store_true", help="调试模式")
    args = parser.parse_args()
    if args.dev:
        app.run(host=args.host, port=args.port, debug=True, threaded=True)
    else:
        from waitress import serve
        print(f"AIGateway 运行于 http://{args.host}:{args.port}  (管理台 /  大屏 /screen)")
        serve(app, host=args.host, port=args.port, threads=32)
