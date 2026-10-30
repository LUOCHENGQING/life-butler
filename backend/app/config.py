"""集中配置：从 .env 读取 DeepSeek 密钥与模型名。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL_FAST: str = os.getenv("DEEPSEEK_MODEL_FAST", "deepseek-flash")
MODEL_PRO: str = os.getenv("DEEPSEEK_MODEL_PRO", "deepseek-v4-pro")

DB_PATH: Path = BASE_DIR / "data" / "life.db"

# 单笔支出超过该金额时，记账操作需要用户人工确认（LangGraph interrupt）
APPROVAL_THRESHOLD_CNY: float = float(os.getenv("APPROVAL_THRESHOLD_CNY", "200"))

# ---------------------------------------------------------------- CORS
# 桌面端（localhost:3000） + 手机端（局域网 IP:3001 / 同网段任意端口）
# 手机走局域网访问时来源是 http://192.168.x.x:3001，这里用正则放行私网段。
FRONTEND_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

# 手机端走局域网，来源随 IP 变化，统一用正则匹配私网地址
FRONTEND_ORIGIN_REGEX = (
    r"^http://("
    r"localhost|127\.0\.0\.1|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"):(3000|3001|8080)$"
)

if not DEEPSEEK_API_KEY:
    raise RuntimeError("缺少 DEEPSEEK_API_KEY，请检查 backend/.env")
