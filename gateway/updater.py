"""版本更新检查与一键更新

机制:
- 项目不使用 tag 发布,版本对比基于 commit:本地 HEAD vs 远端分支 HEAD
- 版本检查:git fetch 远端分支(仅写入 FETCH_HEAD,不影响工作区/本地分支),
  再统计 HEAD..FETCH_HEAD 落后提交数,并读取远端最新提交的 SHA/时间/标题
- 一键更新(方式可在控制台配置):
    - direct:git fetch + git merge --ff-only,成功后可选自动重启当前进程
      (先拉起独立重启辅助进程,旧进程让出端口后由辅助进程以原参数重新拉起服务)
    - docker :git fetch + git merge --ff-only,成功后可选 docker compose up -d --build
- 安全:所有命令均以参数列表方式执行(不经 shell);仓库 URL / 分支名输入严格校验,防命令注入
"""
import os
import re
import subprocess
import sys
import threading
import time

import config

GIT = "git"
# 重启时序:先让旧进程让出端口,再由独立辅助进程拉起新进程(须 _RESTART_DELAY > _EXIT_DELAY)
_EXIT_DELAY = 1.5      # 响应返回后延迟秒数再退出旧进程
_RESTART_DELAY = 5.0   # 新进程延迟启动秒数(避免与旧进程端口冲突)

# 仓库 URL 仅允许 http(s) 或 scp 风格 git@host:path,禁止空格与 shell 元字符
_REPO_URL_RE = re.compile(r"^(https?://|git@)[A-Za-z0-9._~:/@?&=#%+\-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._\-/]+$")

_state = {
    "checking": False,     # 检查进行中
    "applying": False,     # 一键更新进行中
    "last_check": 0.0,     # 最近一次远程检查时间戳(epoch)
    "result": None,        # 最近一次检查结果 dict
    "apply": None,         # 最近一次一键更新结果 dict
}
_lock = threading.Lock()


# ---------- 输入校验(防命令注入) ----------
def validate_repo_url(url):
    """校验更新源仓库 URL:仅允许 http(s) 或 git@host:path 形式"""
    return bool(url and _REPO_URL_RE.match(url.strip()))


def validate_branch(branch):
    """校验分支名:仅允许字母数字 . _ - /,且不能包含路径穿越的 '..'"""
    b = (branch or "").strip()
    return bool(b and ".." not in b and _BRANCH_RE.match(b))


# ---------- 命令执行 ----------
_GIT_ROOT = None     # 缓存的仓库根目录


def _repo_root():
    """从项目根向上查找包含 .git 的仓库根目录(找到后缓存),找不到返回 None"""
    global _GIT_ROOT
    if _GIT_ROOT:
        return _GIT_ROOT
    d = os.path.abspath(config.BASE_DIR)
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            _GIT_ROOT = d
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _run(cmd, timeout=60, cwd=None):
    """执行命令(参数列表,不经 shell),返回 (ok, output)"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, cwd=cwd)
        out = (p.stdout or "").strip()
        if p.returncode != 0:
            return False, (p.stderr or out or f"exit={p.returncode}").strip()
        return True, out
    except FileNotFoundError:
        return False, "未找到命令: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return False, "执行超时(%ss)" % timeout
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _git(*args, timeout=60, cwd=None):
    """执行 git 命令。cwd 默认取仓库根目录(从 BASE_DIR 向上查找 .git),
    避免服务启动目录与仓库不一致时报 'not a git repository'
    (systemd / 任务计划 / 容器内非 WORKDIR 路径启动等场景)。"""
    if cwd is None:
        cwd = _repo_root()
        if not cwd:
            return False, ("当前部署目录不是 git 仓库,无法检查/更新版本。"
                           "请以 git clone 方式部署(勿在 .dockerignore 排除 .git / 勿用非 git 方式拷贝代码)")
    return _run([GIT, *args], timeout=timeout, cwd=cwd)


# ---------- 本地版本 ----------
def current_version(cwd=None):
    """本地版本信息 {sha, short, date, subject};非 git 仓库返回 None"""
    ok, out = _git("show", "-s", "--format=%H%n%cI%n%s", "HEAD",
                   cwd=cwd or _repo_root() or config.BASE_DIR)
    if not ok:
        return None
    lines = out.split("\n")
    sha = (lines[0] or "").strip()
    if not sha:
        return None
    return {
        "sha": sha,
        "short": sha[:7],
        "date": lines[1].strip() if len(lines) > 1 else "",
        "subject": lines[2].strip() if len(lines) > 2 else "",
    }


def _candidate_repos(settings):
    """生成去重后的候选更新源列表(主源优先,备用源兜底,均需通过 URL 校验)"""
    seen, out = set(), []
    for key in ("repo", "fallback_repo"):
        url = (settings.get(key) or "").strip()
        if url and validate_repo_url(url) and url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------- 检查远端版本 ----------
def check_update(settings, force=False):
    """检查远端更新(阻塞数秒)。settings 需含 repo / fallback_repo / branch。
    按顺序尝试各更新源节点:主源不可达时自动切换备用源;记录实际使用的节点 used_repo。"""
    branch = (settings.get("branch") or "").strip()
    if not validate_branch(branch):
        return _set_result(ok=False, error="分支名无效")
    repos = _candidate_repos(settings)
    if not repos:
        return _set_result(ok=False, error="更新仓库地址无效(主/备用源均未配置或格式错误)")

    with _lock:
        if _state["checking"]:
            return _state["result"] or {"ok": False, "error": "检查进行中,请稍候"}
        _state["checking"] = True
    try:
        cur = current_version()
        errs = []
        for repo in repos:
            ok, out = _git("fetch", "--quiet", repo, branch, timeout=30)
            if not ok:
                errs.append("%s: %s" % (repo, out))
                continue
            ok, cnt = _git("rev-list", "--count", "HEAD..FETCH_HEAD")
            behind = int(cnt) if ok and cnt.strip().isdigit() else 0
            latest = {"sha": "", "short": "", "date": "", "subject": ""}
            ok, sha = _git("rev-parse", "FETCH_HEAD")
            if ok and sha.strip():
                latest["sha"] = sha.strip()
                latest["short"] = sha[:7].strip()
            ok, subj = _git("log", "-1", "--format=%s", "FETCH_HEAD")
            if ok:
                latest["subject"] = subj.strip()
            ok, date = _git("log", "-1", "--format=%cI", "FETCH_HEAD")
            if ok:
                latest["date"] = date.strip()
            return _set_result(ok=True, current=cur, latest=latest,
                               has_update=bool(cur and behind > 0), behind=behind,
                               used_repo=repo)
        tail = ("; ".join(errs))[:400]
        return _set_result(ok=False, error="所有更新源均不可达:" + tail)
    finally:
        with _lock:
            _state["checking"] = False


def _set_result(ok=False, error=None, **extra):
    res = {"ok": ok, "error": error, **extra}
    with _lock:
        _state["result"] = res
        _state["last_check"] = time.time()
    return res


# ---------- 一键更新 ----------
def apply_update(settings):
    """一键更新(阻塞,请放到后台线程执行)。settings 需含 repo / fallback_repo / branch / mode / auto_restart
    按顺序尝试各更新源节点:主源不可达时自动切换备用源;记录实际使用的节点 used_repo"""
    mode = (settings.get("mode") or "direct").strip().lower()
    branch = (settings.get("branch") or "").strip()
    used_repo = None
    if mode not in ("direct", "docker"):
        return _apply_result(ok=False, message="未知更新方式: %s" % mode)
    if not validate_branch(branch):
        return _apply_result(ok=False, message="分支名无效")
    repos = _candidate_repos(settings)
    if not repos:
        return _apply_result(ok=False, message="更新仓库地址无效(主/备用源均未配置或格式错误)")
    with _lock:
        if _state["applying"]:
            return _state["apply"] or {"ok": False, "message": "更新已在进行中"}
        _state["applying"] = True
        _state["apply"] = {"ok": False, "message": "执行中...", "steps": [],
                           "needs_restart": False, "started_at": time.time(), "finished_at": None}
    steps = []
    try:
        errs = []
        for repo in repos:
            st = _step(["git", "fetch", "--quiet", repo, branch], timeout=120)
            steps.append(st)
            if not st["ok"]:
                errs.append("%s: %s" % (repo, st["err"]))
                continue
            st = _step(["git", "merge", "--ff-only", "FETCH_HEAD"], timeout=60)
            steps.append(st)
            if not st["ok"]:
                return _apply_result(ok=False, message="合并失败(请确认无未提交的本地修改): " + st["err"],
                                     steps=steps)
            used_repo = repo
            break
        if used_repo is None:
            tail = ("; ".join(errs))[:400]
            return _apply_result(ok=False, message="所有更新源均不可达(拉取失败): " + tail, steps=steps)

        if mode == "docker":
            if not settings.get("auto_restart"):
                return _apply_result(ok=True, message="代码已更新;已按设置跳过容器重建,请手动执行 docker compose up -d --build",
                                     steps=steps)
            if not _find_compose():
                return _apply_result(ok=False, message="未找到 docker-compose.yml / compose.yml,无法以 Docker 方式重建",
                                     steps=steps)
            st = _step(["docker", "compose", "up", "-d", "--build"],
                       cwd=config.BASE_DIR, timeout=600)
            steps.append(st)
            if not st["ok"]:
                return _apply_result(ok=False, message="Docker 重建失败: " + st["err"], steps=steps)
            return _apply_result(ok=True, message="代码与容器均已更新完成", steps=steps, used_repo=used_repo)

        # direct 模式
        if not settings.get("auto_restart"):
            return _apply_result(ok=True, message="代码已更新;已按设置跳过自动重启,请手动重启服务使新代码生效",
                                 steps=steps)
        if not _spawn_restart():
            return _apply_result(ok=False, message="代码已更新,但自动重启启动失败,请手动重启服务", steps=steps)
        return _apply_result(ok=True, message="代码已更新,服务将在几秒内自动重启(期间页面可能短暂中断)",
                             steps=steps, needs_restart=True, used_repo=used_repo)
    except Exception as e:  # noqa: BLE001
        return _apply_result(ok=False, message="更新异常: %s" % e, steps=steps)
    finally:
        with _lock:
            _state["applying"] = False


def _apply_result(ok, message, steps=None, needs_restart=False, used_repo=None):
    res = {"ok": ok, "message": message, "steps": steps or [],
           "needs_restart": needs_restart,
           "started_at": _state["apply"].get("started_at", time.time()) if _state["apply"] else time.time(),
           "finished_at": time.time()}
    if used_repo:
        res["used_repo"] = used_repo
    with _lock:
        _state["apply"] = res
    return res


def _step(cmd, timeout=60, cwd=None):
    """执行一步更新命令并记录结果(step 用于前端展示)"""
    ok, out = _run(cmd, timeout=timeout, cwd=cwd)
    return {"name": cmd[0], "cmd": " ".join(cmd),
            "ok": ok, "out": out if ok else "", "err": "" if ok else out}


def _find_compose():
    for name in ("docker-compose.yml", "compose.yml", "docker-compose.yaml", "compose.yaml"):
        p = os.path.join(config.BASE_DIR, name)
        if os.path.exists(p):
            return p
    return None


def _spawn_restart():
    """direct 模式重启:拉起独立辅助进程(延迟后以原启动参数重启服务),随后延迟退出旧进程"""
    restart_cmd = [sys.executable, "-m", "gateway.restarter", str(_RESTART_DELAY),
                   sys.executable, *sys.argv]
    try:
        flags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs = {"creationflags": flags} if flags else {"start_new_session": True}
        subprocess.Popen(restart_cmd, cwd=config.BASE_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
    except Exception:  # noqa: BLE001
        return False
    threading.Thread(target=_delayed_exit, args=(_EXIT_DELAY,), daemon=True).start()
    return True


def _delayed_exit(seconds):
    """等待若干秒后退出当前进程(给响应写完留出时间)"""
    time.sleep(seconds)
    os._exit(0)


# ---------- 设置 ----------
def get_settings():
    """读取更新相关设置(需 app 上下文)"""
    from .models import Setting

    def _g(key, default):
        try:
            v = Setting.get(key)
        except Exception:  # noqa: BLE001
            v = None
        return v if v not in (None, "") else default

    return {
        "enabled": _g("update_enabled", "1") == "1",
        "repo": _g("update_repo", config.UPDATE_REPO),
        "fallback_repo": _g("update_repo_fallback", config.UPDATE_REPO_FALLBACK),
        "branch": _g("update_branch", config.UPDATE_BRANCH),
        "mode": _g("update_mode", "direct"),
        "interval_hours": int(_g("update_check_interval", "6") or 0),
        "auto_restart": _g("update_auto_restart", "1") == "1",
    }


# ---------- 状态 ----------
def status():
    """组装更新状态:当前版本 / 是否有新版本 / 更新方式 / 最近检查与更新记录。
    若距上次检查超过检查间隔,自动在后台线程刷新(不阻塞请求)。"""
    settings = get_settings()
    cur = current_version()
    with _lock:
        result = dict(_state["result"] or {})
        applying = _state["applying"]
        apply = dict(_state["apply"] or {}) if _state["apply"] else None
        last_check = _state["last_check"]
        checking = _state["checking"]

    # 超间隔自动后台刷新
    if (settings.get("enabled") and settings.get("interval_hours") and not applying
            and not checking and time.time() - last_check >= settings["interval_hours"] * 3600):
        _thread_check(settings)

    return {
        "enabled": settings.get("enabled", True),
        "mode": settings.get("mode", "direct"),
        "auto_restart": settings.get("auto_restart", True),
        "interval_hours": settings.get("interval_hours", 6),
        "repo": settings.get("repo"),
        "fallback_repo": settings.get("fallback_repo"),
        "branch": settings.get("branch"),
        "used_repo": result.get("used_repo") or (apply or {}).get("used_repo"),
        "current": cur,
        "has_update": bool(result.get("has_update")),
        "behind": result.get("behind", 0),
        "latest": result.get("latest"),
        "result_ok": result.get("ok"),
        "result_error": result.get("error"),
        "last_check": last_check,
        "checking": checking,
        "applying": applying,
        "apply": apply,
    }


def _thread_check(settings):
    def _do():
        try:
            check_update(settings)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=_do, daemon=True, name="update-check").start()