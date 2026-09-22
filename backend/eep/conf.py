"""配置加载 —— 环境变量 > config/settings.local.yaml > config/settings.example.yaml > 代码默认值。

《项目规范》5.3 要求配置分层；这里只做「读」和「取」，不含业务逻辑。
敏感项（数据库口令、平台 token）一律走环境变量或 settings.local.yaml（已 gitignore）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
CONFIG_DIR = BASE_DIR / "config"

#: 本地真实配置优先；没有就退回示例配置（示例里口令都是占位符，能跑但连不上库）
CONFIG_FILE: Path | None = None
for _name in ("settings.local.yaml", "settings.example.yaml"):
    _p = CONFIG_DIR / _name
    if _p.exists():
        CONFIG_FILE = _p
        break

if CONFIG_FILE is None:  # pragma: no cover
    raise RuntimeError(f"未找到配置文件，请从 config/settings.example.yaml 复制一份到 {CONFIG_DIR}")

with CONFIG_FILE.open(encoding="utf-8") as _f:
    CONFIG: dict[str, Any] = yaml.safe_load(_f) or {}


def get(path: str, default: Any = None) -> Any:
    """按 'database.host' 这样的点号路径取配置。"""
    node: Any = CONFIG
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def env(key: str, default: Any = None) -> Any:
    """读环境变量；空字符串按未设置处理。"""
    val = os.environ.get(key)
    return val if val not in (None, "") else default


def env_bool(key: str, default: bool) -> bool:
    val = env(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")
