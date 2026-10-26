#!/bin/sh
# ══════════════════════════════════════════════════════════════
# 打包离线部署 bundle（在**有源码、能联网构建**的机器上跑）
#
#   cd deploy/docker
#   cp .env.example .env          # 构建阶段 compose 要校验必填项，值可以先随便填
#   ./build-offline-bundle.sh
#
# 产出：dist/eep-docker-bundle-v<版本>-<日期>.tar.gz
#   ├─ eep-images.tar    前端 + 后端 + MySQL 三个镜像（docker save）
#   └─ runtime/          到目标机只需要这一份，不拷源码、不需要外网
#       ├─ docker-compose.yml
#       ├─ .env.example
#       ├─ settings/settings.local.yaml   ← 由 backend/config 的示例生成，
#       │                                    设备登记表/口令必须现场改
#       ├─ load-and-run.sh
#       └─ README.md
#
# 拷到目标机后：
#   tar xzf eep-docker-bundle-*.tar.gz && cd eep-docker-bundle-*/runtime
#   ./load-and-run.sh
#
# Docker Hub 拉不动时给公共镜像加加速前缀（只影响 mysql 这类基础镜像）：
#   REGISTRY_MIRROR=docker.m.daocloud.io ./build-offline-bundle.sh
# ══════════════════════════════════════════════════════════════
set -eu

cd "$(dirname "$0")"

# ⚠ 版本号**只有这一处默认值**：docker-compose.yml 用 `${EEP_VERSION:-1.7.0}` 取同一个默认，
#   所以改版本只需改这两处的默认值（或直接 export EEP_VERSION）。
#   2026-10 曾出现「compose 打 1.1.0、package.json 已 1.5.0」的漂移（见 docs/06 代码审查报告），
#   硬编码两处就是根因 —— 现在靠单一来源消除。
VERSION="${EEP_VERSION:-1.7.0}"
# go2rtc 不在默认清单里：它是可选服务（--profile stream），
# 现场现在跑的是 Windows 版网关，塞进包里只会白白多出 100MB+。
IMAGES="eep-frontend:$VERSION eep-backend:$VERSION mysql:8.0"
MIRROR="${REGISTRY_MIRROR:-}"
OUT_ROOT="${1:-dist}"
BUNDLE="eep-docker-bundle-v${VERSION}-$(date +%Y%m%d)"

command -v docker >/dev/null 2>&1 || {
    echo "错误：本机没有 docker，无法打包。" >&2
    exit 1
}

if [ ! -f .env ]; then
    echo "错误：缺少 .env —— docker compose build 会校验其中的必填项。" >&2
    echo "      先执行 cp .env.example .env 并填好 EEP_DB_PASSWORD 等三项。" >&2
    exit 1
fi

# ── 1. 构建业务镜像 ─────────────────────────────────────────
echo "==> 构建前端与后端镜像"
docker compose build

# ── 2. 备齐公共镜像（离线包必须自包含，目标机不能联网拉镜像）──
ensure_image() {
    name=$1
    if docker image inspect "$name" >/dev/null 2>&1; then
        echo "    已有 $name"
        return 0
    fi
    repo=${name%%:*}
    tag=${name##*:}
    if [ -n "$MIRROR" ]; then
        echo "    从 $MIRROR 拉取 $name"
        docker pull "$MIRROR/library/$repo:$tag"
        docker tag "$MIRROR/library/$repo:$tag" "$name"
    else
        echo "    拉取 $name"
        docker pull "$name"
    fi
}

echo "==> 检查公共镜像"
ensure_image mysql:8.0

# ── 3. 组装 bundle ──────────────────────────────────────────
rm -rf "$OUT_ROOT/$BUNDLE"
mkdir -p "$OUT_ROOT/$BUNDLE/runtime/settings"

echo "==> 导出镜像（几百 MB，需要一两分钟）"
docker save -o "$OUT_ROOT/$BUNDLE/eep-images.tar" $IMAGES

cp docker-compose.yml "$OUT_ROOT/$BUNDLE/runtime/"
cp .env.example       "$OUT_ROOT/$BUNDLE/runtime/"
cp load-and-run.sh    "$OUT_ROOT/$BUNDLE/runtime/"
cp README.md          "$OUT_ROOT/$BUNDLE/runtime/" 2>/dev/null || true
chmod +x "$OUT_ROOT/$BUNDLE/runtime/load-and-run.sh"

# 现场配置：镜像里那份是 example（口令都是占位符），容器要求挂一个真实文件。
# 从唯一真值来源 backend/config/ 复制，避免在 deploy/ 里出现第二份配置模板。
cp ../../backend/config/settings.example.yaml \
   "$OUT_ROOT/$BUNDLE/runtime/settings/settings.local.yaml"

echo "==> 压缩"
tar czf "$OUT_ROOT/$BUNDLE.tar.gz" -C "$OUT_ROOT" "$BUNDLE"

cat <<EOF

打包完成：$OUT_ROOT/$BUNDLE.tar.gz  ($(du -h "$OUT_ROOT/$BUNDLE.tar.gz" | cut -f1))

⚠ 包里的 runtime/settings/settings.local.yaml 是从示例配置复制的，
  其中数据库口令、蒸汽平台 token（params.cid / params.token）、
  进水总表地址仍是占位符 CHANGE_ME。上线前必须现场改好，否则
  蒸汽表那一路采不到数（其余电表走串口服务器，不受影响）。

拷到目标机后执行：
    tar xzf $OUT_ROOT/$BUNDLE.tar.gz
    cd $BUNDLE/runtime && ./load-and-run.sh
EOF
