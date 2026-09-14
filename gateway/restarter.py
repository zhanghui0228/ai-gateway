"""进程重启辅助:延迟若干秒后以原启动参数重新拉起服务。
供 direct 模式一键更新使用(由更新流程预先拉起,旧进程让出端口后接管)。

用法: python -m gateway.restarter <延迟秒> <python可执行> <脚本/模块> [原启动参数...]
日志写入 data/restart.log,便于排查重启失败原因。
"""
import os
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(BASE_DIR, "data", "restart.log")


def main():
    if len(sys.argv) < 3:
        print("用法: python -m gateway.restarter <延迟秒> <python> <script> [args...]")
        return 2
    delay = float(sys.argv[1])
    cmd = sys.argv[2:]
    time.sleep(max(0.0, delay))

    flags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    kwargs = {"start_new_session": True} if not flags else {"creationflags": flags}

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("[%s] 重启服务: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), " ".join(cmd)))
    try:
        with open(LOG, "ab") as log_f:
            subprocess.Popen(cmd, cwd=BASE_DIR, stdin=subprocess.DEVNULL,
                             stdout=log_f, stderr=subprocess.STDOUT, **kwargs)
        return 0
    except Exception as e:  # noqa: BLE001
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] 重启失败: %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), e))
        return 1


if __name__ == "__main__":
    sys.exit(main())