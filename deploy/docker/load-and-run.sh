#!/bin/sh
# ══════════════════════════════════════════════════════════════
# 目标机一键部署（离线，无需源码、无需外网）
#
#   tar xzf eep-docker-bundle-v1.5.2-*.tar.gz
#   cd eep-docker-bundle-v1.5.2-*/runtime
#   ./load-and-run.sh
#
# 脚本做的事：导入镜像 → 生成 .env（随机强口令）→ 检查现场配置 →
#              起五个容器 → 等到健康检查通过再报告访问地址。
# 重复执行是安全的：镜像导入幂等，数据卷不会被删。
# ══════════════════════════════════════════════════════════════
set -eu

cd "$(dirname "$0")"

# ── 前置检查 ────────────────────────────────────────────────
command -v docker >/dev/null 2>&1 || {
    echo "错误：本机没有 docker。请先安装 docker + docker compose v2。" >&2
    echo "      Ubuntu/Debian： sudo apt-get install -y docker.io docker-compose-v2" >&2
    echo "      加当前用户进组： sudo usermod -aG docker \$USER  （重新登录生效）" >&2
    exit 1
}
docker compose version >/dev/null 2>&1 || {
    echo "错误：docker compose (v2) 不可用。装 docker-compose-v2 或把 compose 插件放到 ~/.docker/cli-plugins/。" >&2
    exit 1
}
docker info >/dev/null 2>&1 || {
    echo "错误：docker 守护进程连不上。当前用户可能不在 docker 组，" >&2
    echo "      执行 sudo usermod -aG docker \$USER 后**重新登录**再试。" >&2
    exit 1
}

# ── 1. 导入镜像 ─────────────────────────────────────────────
if [ -f ../eep-images.tar ]; then
    TARBALL=../eep-images.tar
elif [ -f ./eep-images.tar ]; then
    TARBALL=./eep-images.tar
else
    TARBALL=""
fi

if [ -n "$TARBALL" ]; then
    echo "==> 导入镜像（$TARBALL）"
    docker load -i "$TARBALL"
else
    echo "==> 没找到 eep-images.tar，跳过导入，直接用本机已有镜像"
fi

# ── 2. 生成 .env（只在第一次；已存在就原样保留）───────────────
# 口令用十六进制而不是 base64/随机符号：compose 会把 ${...} 插值到 YAML 里，
# 出现 $ 或引号会直接破坏解析，排查起来非常费劲。
hex_secret() {
    head -c "${1:-24}" /dev/urandom | od -v -An -tx1 | tr -d ' \n'
}

if [ ! -f .env ]; then
    # 这台机器以前部署过吗？数据卷在，就说明库里已有真实数据、
    # MySQL 也已用**旧 .env 里的口令**初始化完毕。这时再生成一套随机口令，
    # 后端会一直认证失败，现象却像「镜像坏了」——所以必须停下来问人。
    if [ -n "$(docker volume ls -q --filter name=eep-mysql-data)" ]; then
        cat >&2 <<'MSG'
错误：检测到已存在 eep-mysql-data 数据卷（这台机器以前部署过，库里可能有真实数据），
      但当前目录没有 .env。
      自动生成新口令会和卷里已初始化的 MySQL 账号对不上，后端将一直连不上库。

      请二选一：
        ① 把上次部署的 .env 拷到本目录，再重新执行本脚本（推荐）
        ② 确认可以清空重来（会删掉全部业务数据，不可恢复）：
             docker compose down && docker volume rm eep_eep-mysql-data
MSG
        exit 1
    fi
    echo "==> 生成 .env（自动填充随机密钥与数据库口令）"
    [ -f .env.example ] || { echo "错误：缺少 .env.example" >&2; exit 1; }
    SECRET_KEY=$(hex_secret 32)
    DB_PASSWORD=$(hex_secret 16)
    DB_ROOT_PASSWORD=$(hex_secret 16)
    sed -e "s|^EEP_SECRET_KEY=.*|EEP_SECRET_KEY=$SECRET_KEY|" \
        -e "s|^EEP_DB_PASSWORD=.*|EEP_DB_PASSWORD=$DB_PASSWORD|" \
        -e "s|^EEP_DB_ROOT_PASSWORD=.*|EEP_DB_ROOT_PASSWORD=$DB_ROOT_PASSWORD|" \
        .env.example > .env
    chmod 600 .env
    echo "    已写入 .env（权限 600）。口令都在这个文件里，别提交、别外传。"
else
    echo "==> 已存在 .env，沿用现有配置"
fi

# ── 3. 现场配置检查 ─────────────────────────────────────────
CONFIG=settings/settings.local.yaml
if [ ! -f "$CONFIG" ]; then
    # 这里必须硬失败：compose 用的是 bind 挂载 + create_host_path:false，
    # 文件不存在时 docker 会直接报错；提前检查是为了给出「怎么办」而不是
    # 一长串挂载错误。
    cat >&2 <<'MSG'
错误：缺少 settings/settings.local.yaml（设备登记表与采集参数）。
      离线包里应当已带一份从 backend/config/settings.example.yaml 生成的副本；
      如果是手工铺的目录，请从源码机器复制一份过来再执行本脚本。
MSG
    exit 1
fi

if grep -q "CHANGE_ME" "$CONFIG"; then
    echo "⚠ 提醒：$CONFIG 里仍有 CHANGE_ME 占位值 —— 通常是蒸汽平台 token、"
    echo "  进水总表地址或数据库口令。不改也能跑，但那几路采不到数。"
fi

# ── 4. 端口与启动 ───────────────────────────────────────────
HTTP_PORT=$(sed -n 's/^EEP_HTTP_PORT=\(.*\)$/\1/p' .env | tr -d ' ')
HTTP_PORT=${HTTP_PORT:-80}

# 目标机上 80 端口经常已被既有的 nginx / apache 占用。
# 不预检的话 docker 只会吐一句 "failed to bind host port 0.0.0.0:80/tcp:
# address already in use"，现场完全不知道该动哪里。
port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${1}$"
    else
        return 1        # 没探测工具就别拦路，交给 docker 自己报
    fi
}

if port_busy "$HTTP_PORT"; then
    cat >&2 <<MSG
错误：${HTTP_PORT} 端口已被本机其它进程占用，前端容器起不来。
      占用者多半是机器上原有的 nginx / apache：
          ss -ltnp | grep ':${HTTP_PORT} '

      二选一：
        ① 改端口（不动原有服务）：编辑 .env 把 EEP_HTTP_PORT 换成 8080 之类，
           然后重新执行本脚本，访问地址变成 http://<服务器IP>:8080/admin/
        ② 停掉占用 80 的服务：sudo systemctl stop nginx && sudo systemctl disable nginx
MSG
    exit 1
fi

echo "==> 启动容器（对外端口 ${HTTP_PORT}）"
# --no-build：目标机没有源码树（compose 里的 build.context 指向 ../.. ），
# 镜像已经由 docker load 准备好，这里只允许用现成的镜像。
docker compose up -d --no-build

# ── 5. 等到健康检查通过（别让人对着还在初始化的页面反复刷新）──
FRONT=http://127.0.0.1:${HTTP_PORT}

echo "==> 等待容器就绪（MySQL 首次建卷约 30~60 秒）"
i=0
while [ "$i" -lt 90 ]; do
    if docker compose ps --status running >/dev/null 2>&1 && curl -fsS "$FRONT/healthz" >/dev/null 2>&1; then
        break
    fi
    i=$((i + 1))
    sleep 2
done

echo
docker compose ps
echo

if curl -fsS "$FRONT/healthz" >/dev/null 2>&1; then
    cat <<EOF
✅ 前端已就绪：
   后台  ${FRONT}/admin/
   大屏  ${FRONT}/screen/
   接口  ${FRONT}/api/energy/overview

   局域网访问请把 127.0.0.1 换成本机 IP；本机 IP 用： hostname -I

下一步排查用得上：
   docker compose logs -f backend      # 接口
   docker compose logs -f collector    # 每轮采集结果（现场最需要看这个）
   docker compose ps                   # 健康状态
EOF
else
    cat >&2 <<EOF
❌ 前端在 180 秒内没有通过健康检查。按顺序看这三处：
   docker compose ps                          # 哪个容器没起来 / 在 restarting
   docker compose logs --tail=50 frontend     # nginx 配置错误
   docker compose logs --tail=50 backend      # 连不上库 / 迁移失败
EOF
    exit 1
fi
