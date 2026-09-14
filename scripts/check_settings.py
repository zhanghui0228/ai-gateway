import sqlite3, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from gateway.auth import Setting, fix_settings
from app import app

db_path = os.path.join(config.DATA_DIR, "gateway.db")
c = sqlite3.connect(db_path)

# 1. 确认当前值
cur = c.execute("select value from settings where key='update_repo_fallback'").fetchone()[0]
print("修复前:", cur)

# 2. 在 app 上下文中执行自愈
with app.app_context():
    fix_settings()

# 3. 确认修复后
cur2 = c.execute("select value from settings where key='update_repo_fallback'").fetchone()[0]
print("修复后:", cur2)
print("已修正:", cur2 == config.UPDATE_REPO_FALLBACK)
