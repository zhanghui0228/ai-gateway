"""全局配置"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 密钥持久化,避免每次重启后登录态失效
_SECRET_FILE = os.path.join(DATA_DIR, ".secret_key")
if os.path.exists(_SECRET_FILE):
    with open(_SECRET_FILE, "r", encoding="utf-8") as f:
        SECRET_KEY = f.read().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    with open(_SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(SECRET_KEY)

SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(DATA_DIR, "gateway.db").replace("\\", "/")
SQLALCHEMY_TRACK_MODIFICATIONS = False
JSON_AS_ASCII = False

# 默认管理员(首次启动初始化,可在控制台修改密码;可用环境变量 GW_ADMIN_PASSWORD 覆盖)
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASSWORD = os.environ.get("GW_ADMIN_PASSWORD", "admin123")

# 转发默认参数(可在控制台设置中覆盖)
DEFAULT_TIMEOUT = 120          # 渠道请求超时(秒)
DEFAULT_RETRY = 3              # 故障转移最大尝试渠道数
BREAKER_THRESHOLD = 5          # 连续失败N次触发熔断
BREAKER_COOLDOWN = 60          # 熔断冷却(秒),冷却后进入半开放行一次探活

# 大屏 SSE 心跳间隔(秒)
SSE_HEARTBEAT = 15

# 响应缓存默认设置(可在控制台设置中覆盖)
CACHE_DEFAULT_TTL = 300        # 缓存有效期(秒),chat/completions 默认 5 分钟
CACHE_MAX_MEMORY = 200         # 内存 LRU 最大条目数
CACHE_MAX_SQLITE = 10000       # SQLite 持久化最大缓存条目数

# 版本更新设置默认值(可在控制台"系统设置 → 更新设置"中覆盖)
UPDATE_REPO = "https://github.com/zhanghui0228/ai-gateway.git"          # 主更新源仓库 URL
UPDATE_REPO_FALLBACK = "https://gitcode.com/zhanghui0228/ai-gateway.git"  # 备用更新源仓库 URL(主源不可达时自动切换)
UPDATE_BRANCH = "main"                                             # 更新源分支
