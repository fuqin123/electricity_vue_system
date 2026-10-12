#!/bin/sh
# ══════════════════════════════════════════════════════════════
# 后端容器入口：等数据库 → 可选建库 → 迁移 → 同步登记表 → exec 主进程
#
# backend 与 collector 两个容器共用这个入口，靠环境变量区分职责：
#   EEP_AUTO_MIGRATE / EEP_AUTO_SEED   backend=true，collector=false
#   （否则两个容器同时 migrate，MySQL 上会撞 DDL 锁）
# ══════════════════════════════════════════════════════════════
set -e

CONFIG_FILE=/app/config/settings.local.yaml

# ── 配置文件挂载自检 ────────────────────────────────────────
# bind mount 的源文件在宿主机上不存在时，Docker 不会报错，而是建一个
# 「同名的空目录」挂上来；Django 侧就变成 IsADirectoryError，现场完全
# 猜不到真实原因是「文件没建」。这里提前给出可执行的修复命令。
if [ -d "$CONFIG_FILE" ]; then
    echo "[eep] 错误：$CONFIG_FILE 是目录 —— 宿主机上没有这个文件，Docker 自动建了空目录。" >&2
    echo "[eep] 请执行：cp backend/config/settings.example.yaml deploy/docker/settings/settings.local.yaml" >&2
    exit 1
fi

# ── 等数据库端口 ────────────────────────────────────────────
# mysql 容器首次初始化（建库 + 建系统表）要 30~60s，期间 3306 还没监听。
# gunicorn 先起来不会崩，但每个请求都是 500，界面上看起来像"后端坏了"，
# 所以先把端口等出来再干活。
DB_HOST="${EEP_DB_HOST:-mysql}"
DB_PORT="${EEP_DB_PORT:-3306}"
WAIT_SECONDS="${EEP_DB_WAIT_SECONDS:-120}"

echo "[eep] 等待数据库 ${DB_HOST}:${DB_PORT} （最多 ${WAIT_SECONDS}s）..."
python - "$DB_HOST" "$DB_PORT" "$WAIT_SECONDS" <<'PY'
import socket, sys, time

host, port, limit = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
deadline = time.time() + limit
while True:
    try:
        with socket.create_connection((host, port), timeout=3):
            print(f"[eep] 数据库已可达：{host}:{port}", flush=True)
            break
    except OSError:
        if time.time() > deadline:
            sys.exit(f"[eep] 错误：等不到数据库 {host}:{port}（{limit}s 超时）")
        time.sleep(2)
PY

cd /app

# ── 空库首次启动时建库 ──────────────────────────────────────
# mysql 官方镜像只在建卷的第一次按 MYSQL_DATABASE 建库；换成外部/已有的
# MySQL 时那个变量不起作用，所以这里补一步。
# 顺序很关键：先「带着库名连一次」，连得上就说明库已存在 —— 不能直接
# CREATE DATABASE IF NOT EXISTS，因为 MySQL 会先查 CREATE 权限，
# 而 Django 用的普通账号（只有该库的 DML 权限）必然报 1044，
# set -e 之下容器会被当成启动失败反复重启。
if [ "${EEP_AUTO_CREATE_DB:-true}" = "true" ]; then
    python - <<'PY'
import os
import sys

import pymysql

params = dict(
    host=os.environ.get("EEP_DB_HOST", "mysql"),
    port=int(os.environ.get("EEP_DB_PORT", "3306")),
    user=os.environ.get("EEP_DB_USER", "root"),
    password=os.environ.get("EEP_DB_PASSWORD", ""),
    charset="utf8mb4",
)
db = os.environ.get("EEP_DB_NAME", "eep_energy")

try:
    pymysql.connect(**params, database=db).close()
    print(f"[eep] 数据库已存在：{db}", flush=True)
    sys.exit(0)
except pymysql.err.OperationalError as exc:
    if exc.args and exc.args[0] == 1049:  # Unknown database
        pass
    else:
        raise

# 只有「库不存在」才走到这里，此时才需要 CREATE 权限
try:
    with pymysql.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        conn.commit()
    print(f"[eep] 已创建数据库：{db}（utf8mb4）", flush=True)
except pymysql.err.OperationalError as exc:
    if exc.args and exc.args[0] == 1044:  # Access denied (CREATE)
        sys.exit(
            f"[eep] 错误：账号 {params['user']} 无权创建数据库 {db}。\n"
            f"[eep] 请用管理员先建库（字符集 utf8mb4）再启动：\n"
            f"[eep]   CREATE DATABASE `{db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
        )
    raise
PY
fi

# ── 迁移与登记（幂等）──────────────────────────────────────
# 一律用 `python manage.py`：仓库里的 manage.py 没有执行位（644），
# 写成 ./manage.py 在容器里会直接 Permission denied。
if [ "${EEP_AUTO_MIGRATE:-true}" = "true" ]; then
    echo "[eep] migrate..."
    python manage.py migrate --noinput
fi

if [ "${EEP_AUTO_SEED:-true}" = "true" ]; then
    # 两个 seed 都只补「库里没有的」，不覆盖后台改过的记录（source=admin），
    # 所以每次容器启动都可以重跑。
    echo "[eep] seed_registry / seed_params..."
    python manage.py seed_registry
    python manage.py seed_params
fi

echo "[eep] 启动主进程：$*"
exec "$@"
