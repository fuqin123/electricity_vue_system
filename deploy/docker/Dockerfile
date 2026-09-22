# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 前端镜像（多阶段构建）
#
# 构建上下文 = **仓库根目录**（不是 frontend/），因为要一并拷贝
# deploy/docker/nginx.default.conf 到镜像里。
#
# 单独构建：
#   docker build -f deploy/docker/Dockerfile -t eep-frontend:1.1.0 .
#
# 或直接用 compose：
#   cd deploy/docker && docker compose up -d --build
#
# 阶段 1 在容器内装依赖并构建（需要能出网或配好私有源）
# 阶段 2 只保留 Nginx + 静态产物，镜像体积小、无 Node 运行时
# ══════════════════════════════════════════════════════════════

# ── 阶段 1：构建 ────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /src

# 国内镜像源（离线环境可改为公司私有 Nexus 地址）
RUN npm config set registry https://registry.npmmirror.com \
 && corepack enable \
 && corepack prepare pnpm@latest --activate

# 先只拷依赖清单，充分利用 Docker 层缓存：
# 只要依赖没变，改业务代码不会重新装依赖
COPY frontend/package.json       frontend/
COPY frontend/pnpm-lock.yaml     frontend/
COPY frontend/pnpm-workspace.yaml frontend/
COPY frontend/.npmrc             frontend/
COPY frontend/apps/screen/package.json  frontend/apps/screen/
COPY frontend/apps/admin/package.json   frontend/apps/admin/
COPY frontend/packages/shared/package.json frontend/packages/shared/

RUN cd frontend && pnpm install --frozen-lockfile

# 再拷源码构建
COPY frontend/ frontend/
RUN cd frontend && pnpm build

# ── 阶段 2：运行 ────────────────────────────────────────────
FROM nginx:1.27-alpine

# 去掉默认站点，避免与我们的配置抢 80 端口
RUN rm -f /etc/nginx/conf.d/default.conf

# 静态产物
COPY --from=builder /src/frontend/apps/screen/dist /usr/share/nginx/html/screen
COPY --from=builder /src/frontend/apps/admin/dist  /usr/share/nginx/html/admin

# 站点配置（容器内路径前缀为 /usr/share/nginx/html）
COPY deploy/docker/nginx.default.conf /etc/nginx/conf.d/eep.conf

# 时区设为东八区，否则 access_log 时间与现场对不上
RUN apk add --no-cache tzdata curl \
 && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone \
 && apk del tzdata

# 健康检查（compose 的 depends_on.condition 依赖它）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1/healthz || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
