# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 前端镜像（大屏 + 后台，多阶段构建）
#
# 构建上下文 = **仓库根目录**（不是 frontend/），因为要一并拷贝
# deploy/docker/nginx.default.conf 到镜像里。
#
#   docker build -f deploy/docker/Dockerfile -t eep-frontend:1.7.2 .
#   或：cd deploy/docker && docker compose build frontend
#
# 拉不到 Docker Hub 时（国内服务器常见）用 ARG 换成加速地址：
#   --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-alpine
#   --build-arg NGINX_IMAGE=docker.m.daocloud.io/library/nginx:1.27-alpine
# ══════════════════════════════════════════════════════════════

# ⚠ 两个 ARG 必须都写在第一个 FROM 之前：只有那时声明的 ARG 才是全局的、
#   可被后续任意 FROM 行插值。写在 stage 之间的 ARG 对紧随其后的 FROM
#   也不可见（报「base name (${NGINX_IMAGE}) should not be blank」）。
ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:1.27-alpine

# ── 阶段 1：构建 ────────────────────────────────────────────
FROM ${NODE_IMAGE} AS builder

WORKDIR /src/frontend

ENV CI=1 \
    npm_config_registry=https://registry.npmmirror.com

# 固定 pnpm 版本，保证任何机器上构建结果一致。
# 不用 corepack：它要校验包签名，走国内镜像源经常以
# 「Cannot find matching keyid」失败。
RUN npm install -g pnpm@10.34.6

# 先只拷依赖清单，充分利用 Docker 层缓存：
# 只要依赖没变，改业务代码不会重新装依赖
# ⚠ COPY 的目标路径是相对 WORKDIR(/src/frontend) 解析的，
#   所以这里一律写 ./、apps/screen/ 这种形式，不要带 frontend/ 前缀。
COPY frontend/package.json       ./
COPY frontend/pnpm-lock.yaml     ./
COPY frontend/pnpm-workspace.yaml ./
COPY frontend/.npmrc             ./
COPY frontend/apps/screen/package.json  apps/screen/
COPY frontend/apps/admin/package.json   apps/admin/
COPY frontend/packages/shared/package.json packages/shared/

# ⚠ 关键修正：仓库里的 pnpm 配置把 store 目录写成 Windows 盘符路径
#   （storeDir: E:\能源产效管理平台\.pnpm-store）。Linux 容器内它会被
#   当成一个名叫 "E:\能源..." 的相对目录，缓存路径不稳定、极端情况下
#   install 直接失败。只改容器里这份拷贝，不动仓库文件
#   （Windows 开发机仍需原路径，E:\ 根目录写入权限不足）。
RUN sed -i 's|^storeDir:.*|storeDir: /pnpm-store|' pnpm-workspace.yaml \
 && sed -i 's|^store-dir=.*|store-dir=/pnpm-store|' .npmrc

RUN pnpm install --frozen-lockfile --store-dir /pnpm-store

# 再拷源码构建
COPY frontend/ ./

# ⚠ 关键修正：后台必须带 --base=/admin/ 构建。
#   源码里 base:'/' 是给「后台部署在站点根目录」那种形态用的，而 Nginx
#   把后台放在 /admin/ 子路径下；不带 base 构建出的 index.html 引用
#   /assets/xxx.js，在容器里必然 404（后台整片白屏）。
#   用 CLI 覆盖而不是改 vite.config.ts，两种部署形态都保留。
#   这里跳过 vue-tsc 只做 vite build：类型检查归 CI，镜像构建不该被
#   类型报错卡住，且 vue-tsc 峰值约 1.5GB，小内存服务器容易 OOM。
RUN pnpm -F @eep/screen exec vite build \
 && pnpm -F @eep/admin exec vite build --base=/admin/

# ── 阶段 2：运行 ────────────────────────────────────────────
FROM ${NGINX_IMAGE}

# 去掉默认站点，避免与我们的配置抢 80 端口
RUN rm -f /etc/nginx/conf.d/default.conf

# 静态产物
COPY --from=builder /src/frontend/apps/screen/dist /usr/share/nginx/html/screen
COPY --from=builder /src/frontend/apps/admin/dist  /usr/share/nginx/html/admin

# 站点配置（容器内路径前缀为 /usr/share/nginx/html）
COPY deploy/docker/nginx.default.conf /etc/nginx/conf.d/eep.conf

# 时区设为东八区，否则 access_log 时间与现场对不上。
# nginx:1.27-alpine 自带 zoneinfo 与 curl，不需要 apk 装任何包
# （少一步包源依赖，内网构建机更容易跑通）。
RUN ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
 && echo "Asia/Shanghai" > /etc/timezone

# 配置写错应当在 build 阶段就失败，而不是等容器起来后反复重启
# （upstream 解析类错误尤其难查，现场只会看到前端容器一直 restarting）
RUN nginx -t

# 健康检查（compose 的 depends_on.condition 依赖它）
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1/healthz || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
