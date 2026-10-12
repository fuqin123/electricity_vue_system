"""Django 配置。

设计取舍（改动前请先读）：
  · **USE_TZ = False**：现场是单一厂区、设备上送的时间就是本地时间（如蒸汽平台的
    collect_time「2026-09-22 14:25:00」）。若开 UTC，小时归属会因为时区换算出错——
    而这套系统的核心口径就是「每小时差值」，一小时错位整套数字就不可信。
    所以统一用**本地时间朴素 datetime**，时区固定 Asia/Shanghai。
  · 数据库用 MySQL + PyMySQL（纯 Python 驱动，Windows 上免编译）。
  · 采集参数（超时/重试/过滤阈值）从 config/settings.yaml 读，见 eep/conf.py。
"""

from __future__ import annotations

from pathlib import Path

from eep import conf

BASE_DIR = Path(__file__).resolve().parent.parent

# ── 基础 ─────────────────────────────────────────────────────
SECRET_KEY = conf.env("EEP_SECRET_KEY", conf.get("auth.secret_key", "dev-only-CHANGE_ME"))
DEBUG = conf.env_bool("EEP_DEBUG", bool(conf.get("app.debug", False)))
# 环境变量优先（逗号分隔）：反代后 Django 看到的 Host 是浏览器里的地址，
# 光靠配置文件里那两条回环条目，换一台机器就要改一次代码外的配置。
ALLOWED_HOSTS = [h.strip() for h in str(conf.env("EEP_ALLOWED_HOSTS", "")).split(",") if h.strip()] or conf.get("app.allowed_hosts") or ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "corsheaders",
    "apps.energy",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # 必须在 CommonMiddleware 之前
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "eep.urls"
WSGI_APPLICATION = "eep.wsgi.application"
TEMPLATES = [
    {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": False, "OPTIONS": {"context_processors": []}}
]

# ── 数据库（MySQL）────────────────────────────────────────────
_db = conf.get("database", {})
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": conf.env("EEP_DB_NAME", _db.get("name", "eep_energy")),
        "USER": conf.env("EEP_DB_USER", _db.get("user", "root")),
        "PASSWORD": conf.env("EEP_DB_PASSWORD", _db.get("password", "")),
        "HOST": conf.env("EEP_DB_HOST", _db.get("host", "127.0.0.1")),
        "PORT": str(conf.env("EEP_DB_PORT", _db.get("port", 3306))),
        "CONN_MAX_AGE": int(_db.get("conn_max_age", 60)),
        "OPTIONS": {
            "charset": "utf8mb4",
            # 严格模式：宁可直接报错，也不要静默截断/存 0
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION'",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── 时区 / 语言 ──────────────────────────────────────────────
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = False
USE_TZ = False  # 见文件头说明：小时归属必须按本地时间，不做 UTC 换算

# ── 跨域（大屏 / 后台分属不同端口或域名）──────────────────────
CORS_ALLOWED_ORIGINS = conf.get("cors.allowed_origins") or []
CORS_ALLOW_CREDENTIALS = bool(conf.get("cors.allow_credentials", False))
# 内网部署时也允许同网段的调试来源（可通过配置关闭）
CORS_ALLOW_ALL_ORIGINS = bool(conf.get("cors.allow_all", False))

STATIC_URL = "static/"
USE_X_FORWARDED_HOST = True

# ── 日志 ─────────────────────────────────────────────────────
_log = conf.get("logging", {})
_handlers: dict = {"console": {"class": "logging.StreamHandler", "formatter": "plain"}}
if _log.get("file_enabled"):
    Path(_log.get("dir", "logs")).mkdir(parents=True, exist_ok=True)
    _handlers["file"] = {
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(Path(_log.get("dir", "logs")) / "eep-backend.log"),
        "maxBytes": int(_log.get("max_bytes", 52_428_800)),
        "backupCount": int(_log.get("backup_count", 30)),
        "encoding": "utf-8",
        "formatter": "plain",
    }

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)-7s %(name)s | %(message)s"}},
    "handlers": _handlers,
    "root": {"handlers": list(_handlers), "level": _log.get("level", "INFO")},
    "loggers": {
        "django.utils.autoreload": {"level": "WARNING"},
    },
}
