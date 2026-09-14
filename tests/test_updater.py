"""版本更新模块单元测试(离线,git/docker 子进程调用均以 mock 替换)"""
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway import updater

PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _reset_state():
    updater._state.update({"checking": False, "applying": False, "last_check": 0.0,
                           "result": None, "apply": None})


# ================= 输入校验(防命令注入) =================
print("== URL/分支校验 ==")
for u in ["https://github.com/zhanghui0228/ai-gateway.git",
          "git@github.com:zhanghui0228/ai-gateway.git",
          "https://atomgit.com/zhh0228/ai-gateway.git",
          "https://example.com/a/b"]:
    check(f"url 合法: {u}", updater.validate_repo_url(u))
for u in ["", "javascript:alert(1)", "https://a b.com/x", "x; rm -rf /",
          "`id`", "$(touch pwned)", "http://ok.com/x y"]:
    check(f"url 拒绝: {u!r}", not updater.validate_repo_url(u))
for b in ["main", "dev", "release-1.0", "feature/x", "master"]:
    check(f"branch 合法: {b}", updater.validate_branch(b))
for b in ["", "main;rm", "a b", "..", "../..", "$(id)"]:
    check(f"branch 拒绝: {b!r}", not updater.validate_branch(b))


# ================= 版本对比(本地 HEAD vs 远端) =================
print("== 版本对比 ==")

def _fake_git(behind="3", fail_fetch=False):
    def _g(*args, timeout=60, cwd=None):
        if args[0] == "show":
            return True, ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                          "2026-09-14T10:00:00+08:00\n本地提交")
        if args[0] == "fetch":
            if fail_fetch:
                return False, "could not read Username"
            return True, ""
        if args[0] == "rev-list":
            return True, behind
        if args[0] == "log":
            fmt = args[2] if len(args) > 2 else ""
            if fmt == "--format=%s":
                return True, "上游新提交"
            return True, "2026-09-15T09:00:00+08:00"
        if args[0] == "rev-parse":
            return True, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        return True, ""
    return _g


settings = {"repo": "https://github.com/x/y.git", "branch": "main", "enabled": True,
            "interval_hours": 6, "mode": "direct", "auto_restart": True}

with mock.patch.object(updater, "_git", side_effect=_fake_git()):
    _reset_state()
    r = updater.check_update(settings)
check("检查成功", r.get("ok") is True, str(r))
check("检测到新版本", r.get("has_update") is True, str(r))
check("落后提交数=3", r.get("behind") == 3, str(r.get("behind")))
check("当前版本对齐", (r.get("current") or {}).get("short") == "aaaaaaa", str(r))
check("远端版本对齐", (r.get("latest") or {}).get("short") == "bbbbbbb", str(r))
check("远端标题", (r.get("latest") or {}).get("subject") == "上游新提交", str(r))

with mock.patch.object(updater, "_git", side_effect=_fake_git(behind="0")):
    _reset_state()
    r = updater.check_update(settings)
check("无更新(behind=0)", r.get("has_update") is False and r.get("behind") == 0, str(r))

with mock.patch.object(updater, "_git", side_effect=_fake_git(fail_fetch=True)):
    _reset_state()
    r = updater.check_update(settings)
check("拉取失败返回错误", r.get("ok") is False and "所有更新源均不可达" in (r.get("error") or ""), str(r))

_reset_state()
r = updater.check_update({"repo": "x; rm -rf /", "branch": "main"})
check("非法 URL 拒绝", r.get("ok") is False
      and r.get("error") == "更新仓库地址无效(主/备用源均未配置或格式错误)", str(r))
r = updater.check_update({"repo": "https://github.com/x/y.git", "branch": "main;rm"})
check("非法分支拒绝", r.get("ok") is False and r.get("error") == "分支名无效", str(r))


# ================= 一键更新(命令分支) =================
print("== 一键更新 ==")

def _fake_run(cmd, timeout=60, cwd=None):
    if cmd[0] == "git":
        return True, "ok"
    if cmd[0] == "docker":
        return True, "Container rebuilt"
    return True, ""


with mock.patch.object(updater, "_run", side_effect=_fake_run), \
     mock.patch.object(updater, "_spawn_restart", return_value=True) as sp:
    _reset_state()
    r = updater.apply_update(settings)
check("direct 更新成功并自动重启", r.get("ok") is True and r.get("needs_restart") is True, str(r))
check("执行 fetch+merge 两步", len(r.get("steps") or []) == 2 and all(s["ok"] for s in r["steps"]), str(r))
check("触发重启辅助", sp.called)

with mock.patch.object(updater, "_run", side_effect=_fake_run), \
     mock.patch.object(updater, "_spawn_restart", return_value=False):
    _reset_state()
    r = updater.apply_update(settings)
check("自动重启失败有提示", r.get("ok") is False and "自动重启启动失败" in (r.get("message") or ""), str(r))

s_no_restart = dict(settings, auto_restart=False)
with mock.patch.object(updater, "_run", side_effect=_fake_run):
    _reset_state()
    r = updater.apply_update(s_no_restart)
check("仅拉取不重启", r.get("ok") is True and r.get("needs_restart") is False
      and "跳过自动重启" in (r.get("message") or ""), str(r))

s_docker = dict(settings, mode="docker")
with mock.patch.object(updater, "_run", side_effect=_fake_run), \
     mock.patch.object(updater, "_find_compose", return_value=None):
    _reset_state()
    r = updater.apply_update(s_docker)
check("docker 无 compose 文件报错", r.get("ok") is False and "compose" in (r.get("message") or "").lower(), str(r))

with mock.patch.object(updater, "_run", side_effect=_fake_run), \
     mock.patch.object(updater, "_find_compose", return_value="docker-compose.yml"):
    _reset_state()
    r = updater.apply_update(s_docker)
check("docker 更新成功", r.get("ok") is True and len(r.get("steps") or []) == 3, str(r))
check("docker 执行 compose 命令", (r.get("steps") or [])[-1]["cmd"] == "docker compose up -d --build", str(r))

def _fake_run_fail_fetch(cmd, timeout=60, cwd=None):
    if cmd[:2] == ["git", "fetch"]:
        return False, "远端认证失败"
    return True, ""

with mock.patch.object(updater, "_run", side_effect=_fake_run_fail_fetch):
    _reset_state()
    r = updater.apply_update(settings)
check("拉取失败中止更新", r.get("ok") is False and "所有更新源均不可达" in (r.get("message") or ""), str(r))

_reset_state()
r = updater.apply_update(dict(settings, mode="foo"))
check("未知方式拒绝", r.get("ok") is False and "未知更新方式" in (r.get("message") or ""), str(r))


# ================= 多更新源节点(主源 + 备用源) =================
print("== 多更新源节点(主/备用) ==")
s2 = dict(settings, fallback_repo="https://gitcode.com/x/y.git")

def _fake_git_multi(fail_repos=()):
    def _g(*args, timeout=60, cwd=None):
        if args[0] == "show":
            return True, ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
                          "2026-09-14T10:00:00+08:00\n本地提交")
        if args[0] == "fetch":
            if args[2] in fail_repos:
                return False, "Connection was reset"
            return True, ""
        if args[0] == "rev-list":
            return True, "2"
        if args[0] == "log":
            fmt = args[2] if len(args) > 2 else ""
            if fmt == "--format=%s":
                return True, "gitcode 新提交"
            return True, "2026-09-16T09:00:00+08:00"
        if args[0] == "rev-parse":
            return True, "cccccccccccccccccccccccccccccccccccccccc"
        return True, ""
    return _g

# 1. 主源不可达 → 自动切备用源
with mock.patch.object(updater, "_git",
                       side_effect=_fake_git_multi(fail_repos={"https://github.com/x/y.git"})):
    _reset_state()
    r = updater.check_update(s2)
check("主源失败自动切备用", r.get("ok") is True
      and r.get("used_repo") == "https://gitcode.com/x/y.git", str(r))
check("备用源下仍能检测新版本", r.get("has_update") is True and r.get("behind") == 2, str(r))

# 2. 主源可用 → 优先主源
with mock.patch.object(updater, "_git", side_effect=_fake_git_multi()):
    _reset_state()
    r = updater.check_update(s2)
check("主源可用优先主源", r.get("ok") is True
      and r.get("used_repo") == "https://github.com/x/y.git", str(r))

# 3. 主/备用均不可达 → 报错列出两个节点
with mock.patch.object(updater, "_git",
                       side_effect=_fake_git_multi(fail_repos={"https://github.com/x/y.git",
                                                              "https://gitcode.com/x/y.git"})):
    _reset_state()
    r = updater.check_update(s2)
check("全部节点不可达报错", r.get("ok") is False
      and "所有更新源均不可达" in (r.get("error") or "")
      and "gitcode.com" in (r.get("error") or ""), str(r))

# 4. 备用源留空 → 仅尝试主源,失败即整体失败
with mock.patch.object(updater, "_git",
                       side_effect=_fake_git_multi(fail_repos={"https://github.com/x/y.git"})):
    _reset_state()
    r = updater.check_update(settings)
check("备用留空仅主源", r.get("ok") is False
      and "所有更新源均不可达" in (r.get("error") or ""), str(r))

# 5. 主/备用相同 → 去重只拉取一次
_calls = []
def _fake_git_count(*args, timeout=60, cwd=None):
    _calls.append(args)
    return _fake_git()(*args, timeout=timeout, cwd=cwd)
with mock.patch.object(updater, "_git", side_effect=_fake_git_count):
    _reset_state()
    r = updater.check_update(dict(settings, fallback_repo=settings["repo"]))
fetch_n = len([c for c in _calls if c[0] == "fetch"])
check("主备相同时去重只拉一次", r.get("ok") is True and fetch_n == 1, str(fetch_n))

# 6. 非法备用 URL 被过滤,不影响主源
with mock.patch.object(updater, "_git", side_effect=_fake_git_multi()):
    _reset_state()
    r = updater.check_update(dict(settings, fallback_repo="javascript:alert(1)"))
check("非法备用 URL 被过滤", r.get("ok") is True
      and r.get("used_repo") == "https://github.com/x/y.git", str(r))

# 7. _candidate_repos 单元:保序 + 去重 + 过滤非法
check("候选源保序去重", updater._candidate_repos(
    {"repo": "https://a.com/x.git", "fallback_repo": "https://a.com/x.git"}) == ["https://a.com/x.git"],
    str(updater._candidate_repos({"repo": "https://a.com/x.git", "fallback_repo": "https://a.com/x.git"})))
check("候选源均为空", updater._candidate_repos({"repo": "", "fallback_repo": "  "}) == [], str(updater._candidate_repos({"repo": "", "fallback_repo": "  "})))

# 8. apply:主源失败 → 备用源完成更新,记录 used_repo
def _fake_run_multi(cmd, timeout=60, cwd=None):
    # 真实 fetch 命令形如 git fetch --quiet <repo> <branch>,repo 在 index 3
    if cmd[:2] == ["git", "fetch"] and cmd[3].startswith("https://github.com"):
        return False, "Connection was reset"
    return True, "ok"

with mock.patch.object(updater, "_run", side_effect=_fake_run_multi), \
     mock.patch.object(updater, "_spawn_restart", return_value=True):
    _reset_state()
    r = updater.apply_update(s2)
check("apply 主源失败切备用", r.get("ok") is True
      and r.get("used_repo") == "https://gitcode.com/x/y.git", str(r))
check("apply 步骤含两次fetch+一次merge", len(r.get("steps") or []) == 3
      and r["steps"][0]["ok"] is False     # 主源 fetch 失败(记录失败步骤)
      and r["steps"][1]["ok"] is True      # 备用源 fetch 成功
      and r["steps"][2]["ok"] is True, str(r))  # merge 成功

# 9. apply:主/备用均不可达
with mock.patch.object(updater, "_run", side_effect=_fake_run_fail_fetch):
    _reset_state()
    r = updater.apply_update(s2)
check("apply 全部节点不可达", r.get("ok") is False
      and "所有更新源均不可达" in (r.get("message") or ""), str(r))


# ================= 仓库根目录定位(非仓库目录启动场景) =================
print("== 仓库根目录定位 ==")
_root = updater._repo_root()
check("找到仓库根目录", _root is not None
      and os.path.exists(os.path.join(_root, ".git")), str(_root))

_saved_root = updater._GIT_ROOT
updater._GIT_ROOT = None   # 清缓存,模拟独立场景
_tmp = tempfile.mkdtemp()
with mock.patch.object(updater.config, "BASE_DIR", _tmp):
    check("非仓库目录返回 None", updater._repo_root() is None, str(updater._repo_root()))
    _ok, _out = updater._git("rev-parse", "--show-toplevel")
    check("非仓库目录给出明确提示", _ok is False and "不是 git 仓库" in _out, _out)
updater._GIT_ROOT = _saved_root

print(f"\n结果: PASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)