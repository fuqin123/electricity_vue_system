# 能源产效管理系统 · 版本部署（Linux 版）

> 文档版本：v1.1.0 ｜ 最后更新：2026-09-22 ｜ 适用：CentOS 7+ / Rocky Linux 8+ / Ubuntu 20.04+ / 统信 UOS / 麒麟
>
> 本文覆盖三种部署形态：
> **方案 A —— Nginx + systemd**（推荐，生产标准做法）
> **方案 B —— Docker Compose**（适合容器化环境 / 快速交付 / 离线镜像包）
> **方案 C —— 一键部署脚本**（适合现场实施人员，最少手工操作）

> ## ⚠ 配置文件的唯一来源是 `deploy/`
>
> 从 v1.1.0 起，所有可直接使用的配置与脚本已从本文档中抽出，落到 `deploy/` 目录：
>
> | 用途 | 文件 |
> | :--- | :--- |
> | Nginx 站点配置 | `deploy/nginx/eep.conf` |
> | 一键部署 / 更新 / 回滚 | `deploy/linux/deploy.sh` |
> | 后端 systemd 单元 | `deploy/linux/eep-backend.service` |
> | 日志轮转 | `deploy/linux/logrotate.eep` |
> | 每日巡检脚本 | `deploy/linux/daily-check.sh` |
> | Docker 构建与编排 | `deploy/docker/{Dockerfile,docker-compose.yml,nginx.default.conf}` |
> | 视频网关配置 | `deploy/go2rtc/go2rtc.example.yaml` |
>
> **改配置改 `deploy/` 下的文件**，本文档中的代码块仅作讲解与示意，不保证与文件同步。
> 部署资源总览见 `deploy/README.md`。

---

## 一、部署前必读

### 1.1 两个应用的部署形态不同（关键）

| 应用 | 路由模式 | 目录 | 服务端要求 |
| :--- | :--- | :--- | :--- |
| 大屏 | `hash` | `/opt/eep/screen` | **无需** rewrite |
| 后台 | `history` | `/opt/eep/admin` | **必须** `try_files` 回退到 `index.html` |

> 后台不配回退的表现：首页能开，**刷新任意子路由就 404**。这是本项目最常见的部署事故。

### 1.2 生产环境不需要 Node.js

`dist/` 是纯静态文件。推荐**在开发机构建、把产物传到服务器**；
如果 CI/CD 在服务器侧跑，才需要装 Node 22 + pnpm。

### 1.3 端口规划

| 端口 | 用途 |
| :--- | :--- |
| `80` / `443` | Nginx 站点入口 |
| `8000` | Django 后端（仅监听 `127.0.0.1`） |
| `1984` | 视频网关 go2rtc（HTTP API + 播放页 + MSE/HLS/MJPEG） |
| `8555` | 视频网关 go2rtc 的 WebRTC（可选） |

---

## 二、构建产物

在开发机（或构建机）执行：

```bash
cd /path/to/electricity_vue_system
pnpm install
pnpm build

# 打包
tar -czf screen-dist.tar.gz -C frontend/apps/screen/dist .
tar -czf admin-dist.tar.gz  -C frontend/apps/admin/dist  .
```

> 后台若部署在子路径（如 `/admin/`），**必须先改** `frontend/apps/admin/vite.config.ts` 的 `base: '/admin/'`
> 再构建。大屏不用改（已是 `base: './'`）。

上传到服务器：

```bash
scp screen-dist.tar.gz admin-dist.tar.gz root@10.20.31.100:/tmp/
```

---

## 三、方案 A：Nginx + systemd（推荐）

### 3.1 安装 Nginx

```bash
# Rocky / CentOS / 麒麟
sudo dnf install -y nginx

# Ubuntu / Debian / 统信 UOS
sudo apt update && sudo apt install -y nginx

# 验证
nginx -v
```

如果是**内网离线环境**，用本地 RPM/DEB 包安装：

```bash
# CentOS 系
sudo rpm -ivh nginx-*.rpm
# 缺依赖时
sudo yum install -y --disablerepo=* --nogpgcheck ./*.rpm
```

### 3.2 创建目录并部署文件

```bash
sudo mkdir -p /opt/eep/screen /opt/eep/admin /opt/eep/_backup

sudo tar -xzf /tmp/screen-dist.tar.gz -C /opt/eep/screen
sudo tar -xzf /tmp/admin-dist.tar.gz  -C /opt/eep/admin

# 属主改为 nginx 运行用户，避免权限告警
sudo chown -R nginx:nginx /opt/eep      # Debian/Ubuntu 上用户是 www-data
sudo chmod -R 755 /opt/eep

# 确认产物到位
ls -l /opt/eep/screen /opt/eep/admin
```

### 3.3 配置 Nginx

新建 `/etc/nginx/conf.d/eep.conf`：

```nginx
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · Nginx 站点配置
# 大屏：hash 路由，无需 rewrite
# 后台：history 路由，必须 try_files 回退
# ══════════════════════════════════════════════════════════════

# 后端 Django（同机部署，只监听回环，避免被直接访问）
upstream eep_backend {
    server 127.0.0.1:8000;
    keepalive 32;
}

# 视频网关 go2rtc
upstream eep_stream {
    server 127.0.0.1:1984;
    keepalive 16;
}

server {
    listen       80;
    listen       [::]:80;
    server_name  _;

    charset      utf-8;
    root         /opt/eep;
    index        index.html;

    # 上传限制（报表导入等）
    client_max_body_size 20m;

    access_log   /var/log/nginx/eep-access.log  main;
    error_log    /var/log/nginx/eep-error.log   warn;

    # ── 安全响应头 ─────────────────────────────────────────
    add_header X-Content-Type-Options  "nosniff"        always;
    add_header X-Frame-Options         "SAMEORIGIN"     always;
    add_header Referrer-Policy         "same-origin"    always;
    server_tokens off;

    # ── gzip 压缩（大屏 JS 约 1 MB，收益显著）───────────────
    gzip              on;
    gzip_vary         on;
    gzip_comp_level   6;
    gzip_min_length   1024;
    gzip_proxied      any;
    gzip_types        text/plain text/css application/javascript application/json
                      application/xml image/svg+xml font/woff2;

    # ── 大屏（hash 路由）──────────────────────────────────
    location /screen/ {
        alias /opt/eep/screen/;
        try_files $uri $uri/ /screen/index.html;

        # 带内容哈希的产物文件名 → 可长期强缓存
        location ~* ^/screen/assets/.*\.(js|css|woff2?|ttf|png|jpe?g|svg|ico)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }

        # 入口文件绝不缓存，保证发版即生效
        location = /screen/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }

    location = /screen { return 301 /screen/; }

    # ── 后台管理（history 路由）────────────────────────────
    location /admin/ {
        alias /opt/eep/admin/;

        # ★ 关键：未命中的路径回退到 index.html，否则刷新子路由 404
        try_files $uri $uri/ /admin/index.html;

        location ~* ^/admin/assets/.*\.(js|css|woff2?|ttf|png|jpe?g|svg|ico)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }

        location = /admin/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }

    location = /admin { return 301 /admin/; }

    # ── 后端 API 反向代理 ─────────────────────────────────
    location /api/ {
        proxy_pass          http://eep_backend;
        proxy_http_version  1.1;
        proxy_set_header    Host              $host;
        proxy_set_header    X-Real-IP         $remote_addr;
        proxy_set_header    X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header    X-Forwarded-Proto $scheme;
        proxy_set_header    Connection        "";

        proxy_connect_timeout  10s;
        proxy_send_timeout     120s;
        proxy_read_timeout     120s;

        # 后端接口一律不缓存
        add_header Cache-Control "no-store";
    }

    # ── 视频转流（FLV / HLS 长连接）────────────────────────
    location /live/ {
        proxy_pass          http://eep_stream/;
        proxy_http_version  1.1;
        proxy_set_header    Host $host;
        proxy_set_header    X-Real-IP $remote_addr;

        # ⚠ FLV 是长连接：必须关闭缓冲并放宽读超时，否则画面几秒就断
        proxy_buffering          off;
        proxy_cache              off;
        proxy_read_timeout       3600s;
        proxy_send_timeout       3600s;
        chunked_transfer_encoding off;

        # 允许大屏页面跨域拉流
        add_header Access-Control-Allow-Origin  * always;
        add_header Access-Control-Allow-Methods "GET, OPTIONS" always;
    }

    # ── 根路径跳后台 ──────────────────────────────────────
    location = / { return 302 /admin/; }

    # 健康检查（供监控系统探测）
    location = /healthz {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    # 屏蔽敏感文件
    location ~ /\.(?!well-known) { deny all; }
    location ~* \.(tar|tar\.gz|zip|sql|bak|log)$ { deny all; }
}
```

> **`alias` 的尾斜杠很重要**：`location /admin/ { alias /opt/eep/admin/; }` ——
> 两边都要有尾斜杠，否则路径会拼错（变成 `/opt/eep/admin` + `/admin/xxx`）。

启用并校验：

```bash
sudo ln -sf /etc/nginx/conf.d/eep.conf /etc/nginx/conf.d/eep.conf

sudo nginx -t
# 期望输出：
#   nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
#   nginx: configuration file /etc/nginx/nginx.conf test is successful

sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

### 3.4 SELinux 注意事项（CentOS / Rocky / 麒麟）

如果 SELinux 是 Enforcing 状态，Nginx 会**无权读取 `/opt` 下的文件**，表现为 403。

**推荐做法 —— 给目录打正确的 SELinux 标签**（比直接关 SELinux 安全）：

```bash
# 查状态
getenforce

# 给新目录打标签（永久生效）
sudo semanage fcontext -a -t httpd_sys_content_t "/opt/eep(/.*)?"
sudo restorecon -Rv /opt/eep

# 允许 Nginx 发起反向代理连接（proxy_pass 必需）
sudo setsebool -P httpd_can_network_connect 1

# 若日志目录也需要写
sudo semanage fcontext -a -t httpd_log_t "/var/log/nginx(/.*)?"
sudo restorecon -Rv /var/log/nginx
```

> `semanage` 命令来自 `policycoreutils-python-utils` 包：
> `sudo dnf install -y policycoreutils-python-utils`

**临时验证用**（不推荐长期）：

```bash
sudo setenforce 0     # 临时切到 Permissive
```

### 3.5 防火墙放行

```bash
# firewalld（CentOS / Rocky / 麒麟）
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
sudo firewall-cmd --list-all

# 只允许内网网段（更严格）
sudo firewall-cmd --permanent --zone=internal --add-source=10.20.0.0/16
sudo firewall-cmd --permanent --zone=internal --add-service=http
sudo firewall-cmd --reload

# ufw（Ubuntu / Debian / UOS）
sudo ufw allow from 10.20.0.0/16 to any port 80 proto tcp
sudo ufw enable
sudo ufw status verbose
```

### 3.6 后端 Django 服务化（systemd）

后端部署在 `/opt/eep-backend`，用 gunicorn + systemd 常驻：

```ini
# /etc/systemd/system/eep-backend.service
[Unit]
Description=EEP Django Backend (gunicorn)
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=notify
User=eep
Group=eep
WorkingDirectory=/opt/eep-backend

# 环境变量从文件读取（避免密钥写进 unit 文件）
EnvironmentFile=-/opt/eep-backend/.env

# gunicorn：仅监听回环，外部访问统一走 Nginx
ExecStart=/opt/eep-backend/.venv/bin/gunicorn \
          --workers 3 \
          --threads 4 \
          --bind 127.0.0.1:8000 \
          --timeout 120 \
          --access-logfile /var/log/eep/backend-access.log \
          --error-logfile  /var/log/eep/backend-error.log \
          --capture-output \
          config.wsgi:application

ExecReload=/bin/kill -s HUP $MAINPID

Restart=always
RestartSec=5

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/eep-backend /var/log/eep

# 日志（同时交给 journald，便于 journalctl 排查）
StandardOutput=journal
StandardError=journal
SyslogIdentifier=eep-backend

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /sbin/nologin eep
sudo mkdir -p /var/log/eep && sudo chown eep:eep /var/log/eep

sudo systemctl daemon-reload
sudo systemctl enable --now eep-backend
sudo systemctl status eep-backend

# 跟随日志
sudo journalctl -u eep-backend -f --since "10 min ago"
```

### 3.7 日志轮转

```ini
# /etc/logrotate.d/eep
/var/log/eep/*.log {
    daily
    rotate 30
    size 50M
    compress
    delaycompress
    missingok
    notifempty
    create 0640 eep eep
    sharedscripts
    postrotate
        systemctl reload eep-backend > /dev/null 2>&1 || true
    endscript
}
```

```bash
# 手动验证配置（不实际执行）
sudo logrotate -d /etc/logrotate.d/eep

# 强制立即轮转一次
sudo logrotate -f /etc/logrotate.d/eep
```

> 按《代码仓库管理规范》第 5.3 条：**本地日志至少保留 30 天，且按大小滚动**。
> 上面的配置已同时满足（`rotate 30` + `size 50M`）。

### 3.8 验证清单

```bash
# 1. Nginx 配置与状态
sudo nginx -t && systemctl is-active nginx

# 2. 静态文件可访问
curl -sI http://127.0.0.1/screen/ | head -3
curl -sI http://127.0.0.1/admin/  | head -3

# 3. ★ 关键：history 回退是否生效（应返回 200 而不是 404）
curl -s -o /dev/null -w "子路由刷新 HTTP 状态: %{http_code}\n" http://127.0.0.1/admin/system/user

# 4. API 代理是否通
curl -s http://127.0.0.1/api/health || echo "后端未就绪"

# 5. 健康检查
curl -s http://127.0.0.1/healthz
```

逐项人工验证：

- [ ] 浏览器打开 `http://<服务器IP>/` → 自动跳后台登录页
- [ ] `http://<服务器IP>/screen/` 大屏正常，图表渲染完整
- [ ] 大屏按 `2` 进单线详情正常
- [ ] 后台登录成功，**21 个菜单逐个点击**均能打开
- [ ] **刷新 `/admin/system/user` 不 404** ← 最关键
- [ ] 视频网关配好后大屏出图；关掉网关降级为占位而非白屏

---

## 四、方案 B：Docker Compose

适用场景：容器化环境、多环境一致性要求高、需要快速交付。

### 4.1 目录结构

```
eep-docker/
├─ docker-compose.yml
├─ nginx/
│  └─ default.conf
├─ screen/            # 大屏构建产物（构建机产出后拷入）
├─ admin/             # 后台构建产物
└─ .env
```

### 4.2 多阶段构建 Dockerfile

```dockerfile
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 前端镜像（多阶段构建）
# 阶段 1：在容器内构建（需要出网拉依赖）
# 阶段 2：仅保留 Nginx + 静态产物（镜像极小）
# ══════════════════════════════════════════════════════════════

# ── 阶段 1：构建 ────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /src

# 国内镜像源加速
RUN npm config set registry https://registry.npmmirror.com && \
    corepack enable && corepack prepare pnpm@latest --activate

# 先拷依赖清单，利用 Docker 层缓存
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY frontend/apps/screen/package.json  frontend/apps/screen/
COPY frontend/apps/admin/package.json   frontend/apps/admin/
COPY frontend/packages/shared/package.json frontend/packages/shared/

RUN pnpm install --frozen-lockfile

# 再拷源码构建
COPY . .
RUN pnpm build

# ── 阶段 2：运行 ────────────────────────────────────────────
FROM nginx:1.27-alpine

# 移除默认站点
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=builder /src/frontend/apps/screen/dist /usr/share/nginx/html/screen
COPY --from=builder /src/frontend/apps/admin/dist  /usr/share/nginx/html/admin
COPY nginx/default.conf /etc/nginx/conf.d/eep.conf

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 4.3 容器内 Nginx 配置 `nginx/default.conf`

与外网配置基本相同，**只有路径前缀不同**（容器内产物在 `/usr/share/nginx/html/`）：

```nginx
upstream eep_backend {
    server backend:8000;      # docker-compose 服务名
    keepalive 32;
}

server {
    listen 80;
    server_name _;
    charset utf-8;
    root /usr/share/nginx/html;

    client_max_body_size 20m;

    gzip on;
    gzip_comp_level 6;
    gzip_min_length 1024;
    gzip_types text/plain text/css application/javascript application/json
               application/xml image/svg+xml font/woff2;

    add_header X-Content-Type-Options "nosniff" always;
    server_tokens off;

    # 大屏（hash 路由）
    location /screen/ {
        alias /usr/share/nginx/html/screen/;
        try_files $uri $uri/ /screen/index.html;

        location ~* ^/screen/assets/.*\.(js|css|woff2?|png|svg)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }
        location = /screen/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }
    location = /screen { return 301 /screen/; }

    # 后台（history 路由，必须回退）
    location /admin/ {
        alias /usr/share/nginx/html/admin/;
        try_files $uri $uri/ /admin/index.html;

        location ~* ^/admin/assets/.*\.(js|css|woff2?|png|svg)$ {
            expires 365d;
            add_header Cache-Control "public, immutable";
            access_log off;
        }
        location = /admin/index.html {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            expires -1;
        }
    }
    location = /admin { return 301 /admin/; }

    # API
    location /api/ {
        proxy_pass          http://eep_backend;
        proxy_http_version  1.1;
        proxy_set_header    Host              $host;
        proxy_set_header    X-Real-IP         $remote_addr;
        proxy_set_header    X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header    Connection        "";
        proxy_read_timeout  120s;
    }

    # 视频流（长连接）
    location /live/ {
        proxy_pass          http://stream:1984/;
        proxy_http_version  1.1;
        proxy_buffering     off;
        proxy_read_timeout  3600s;
        chunked_transfer_encoding off;
        add_header          Access-Control-Allow-Origin * always;
    }

    location = / { return 302 /admin/; }

    location = /healthz {
        access_log off;
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }
}
```

### 4.4 `docker-compose.yml`

```yaml
services:
  # ── 前端（大屏 + 后台）──────────────────────────────────
  frontend:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    image: eep-frontend:0.1.0
    container_name: eep-frontend
    restart: unless-stopped
    ports:
      - "80:80"
    depends_on:
      backend:
        condition: service_healthy
    networks:
      - eep-net
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"

  # ── 后端 Django ─────────────────────────────────────────
  backend:
    image: eep-backend:0.1.0      # 由后端仓库构建
    container_name: eep-backend
    restart: unless-stopped
    env_file:
      - .env
    expose:
      - "8000"
    volumes:
      - ./logs/backend:/var/log/eep
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s
    networks:
      - eep-net
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"

  # ── 流媒体网关（RTSP → HTTP-FLV）─────────────────────────
  stream:
    image: alexxit/go2rtc:latest
    container_name: eep-stream
    restart: unless-stopped
    ports:
      - "1984:1984"     # HTTP API / 播放页 / MSE / HLS / MJPEG
      - "8555:8555"     # WebRTC（TCP+UDP）
    volumes:
      - ../go2rtc/go2rtc.example.yaml:/config/go2rtc.yaml:ro
    networks:
      - eep-net

networks:
  eep-net:
    driver: bridge
```

### 4.5 常用命令

```bash
# 构建并启动
docker compose up -d --build

# 查看状态与日志
docker compose ps
docker compose logs -f frontend

# 更新前端（重新构建镜像）
docker compose build frontend
docker compose up -d frontend

# 滚动重启（不中断服务）
docker compose up -d --no-deps --build frontend

# 回滚到上一镜像
docker tag eep-frontend:0.1.0 eep-frontend:rollback
docker compose down frontend
docker compose up -d frontend

# 清理无用镜像（注意不要误删 rollback 标签的）
docker image prune -f
```

### 4.6 离线环境交付

内网 Docker 环境无法拉镜像时，用 `save` / `load` 传递：

```bash
# ── 在能出网的构建机上 ───────────────────────────────
docker compose build
docker save eep-frontend:0.1.0 eep-backend:0.1.0 \
  -o eep-images-0.1.0.tar
docker save nginx:1.27-alpine alexxit/go2rtc:latest \
  -o eep-3rdparty.tar
gzip eep-images-0.1.0.tar eep-3rdparty.tar

# ── 拷到内网服务器 ──────────────────────────────────
gunzip eep-images-0.1.0.tar.gz eep-3rdparty.tar.gz
docker load -i eep-3rdparty.tar
docker load -i eep-images-0.1.0.tar
docker compose up -d
```

---

## 五、方案 C：一键部署脚本

适合现场实施人员：把「解压 + 配置 + 校验 + 启停 + 备份 + 回滚」全部固化。

### 5.1 部署脚本 `deploy.sh`

```bash
#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# 能源产效管理系统 · 一键部署脚本
# 用法：
#   ./deploy.sh install          首次安装
#   ./deploy.sh update <tar包>   更新（自动备份，失败可回滚）
#   ./deploy.sh rollback         回滚到最近一次备份
#   ./deploy.sh status           查看状态
# ══════════════════════════════════════════════════════════════
set -euo pipefail

APP_ROOT="/opt/eep"
BACKUP_DIR="${APP_ROOT}/_backup"
NGINX_CONF="/etc/nginx/conf.d/eep.conf"
KEEP_BACKUPS=5

RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; NC=$'\033[0m'
info()  { echo "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo "${YELLOW}[WARN]${NC}  $*"; }
error() { echo "${RED}[ERROR]${NC} $*" >&2; }

need_root() {
  if [[ $EUID -ne 0 ]]; then
    error "请用 root 或 sudo 执行"
    exit 1
  fi
}

# ── 环境自检 ────────────────────────────────────────────────
preflight() {
  info "环境自检..."
  command -v nginx >/dev/null 2>&1 || { error "未安装 nginx，请先安装"; exit 1; }
  if command -v getenforce >/dev/null 2>&1; then
    warn "SELinux 状态：$(getenforce)（若为 Enforcing，需执行 semanage + restorecon，见部署文档 3.4）"
  fi
  local avail
  avail=$(df -m "${APP_ROOT%/*}" 2>/dev/null | awk 'NR==2{print $4}')
  if [[ -n "${avail}" && "${avail}" -lt 1024 ]]; then
    warn "磁盘可用空间不足 1GB（当前 ${avail} MB），备份可能失败"
  fi
}

# ── 首次安装 ────────────────────────────────────────────────
do_install() {
  need_root
  preflight

  local screen_tar="${1:-}" admin_tar="${2:-}"
  [[ -f "${screen_tar}" ]] || { error "大屏产物包不存在：${screen_tar}"; exit 1; }
  [[ -f "${admin_tar}"  ]] || { error "后台产物包不存在：${admin_tar}"; exit 1; }

  info "创建目录"
  mkdir -p "${APP_ROOT}/screen" "${APP_ROOT}/admin" "${BACKUP_DIR}"

  info "解压产物"
  tar -xzf "${screen_tar}" -C "${APP_ROOT}/screen"
  tar -xzf "${admin_tar}"  -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}"
  chmod -R 755 "${APP_ROOT}"

  info "写入 Nginx 配置"
  if [[ ! -f "${NGINX_CONF}" ]]; then
    cp "$(dirname "$0")/eep.conf" "${NGINX_CONF}"
  else
    warn "${NGINX_CONF} 已存在，跳过（如需更新请手工替换）"
  fi

  info "SELinux 标签（若启用）"
  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    semanage fcontext -a -t httpd_sys_content_t "${APP_ROOT}(/.*)?" 2>/dev/null || true
    restorecon -Rv "${APP_ROOT}" || true
    setsebool -P httpd_can_network_connect 1 || true
  fi

  info "校验并重载 Nginx"
  nginx -t || { error "Nginx 配置校验失败，请检查 ${NGINX_CONF}"; exit 1; }
  systemctl enable --now nginx
  systemctl reload nginx

  do_verify
}

# ── 更新（带自动备份）────────────────────────────────────────
do_update() {
  need_root

  local admin_tar="${1:-}"
  [[ -f "${admin_tar}" ]] || { error "后台产物包不存在：${admin_tar}"; exit 1; }

  local ts; ts=$(date +%Y%m%d-%H%M%S)
  mkdir -p "${BACKUP_DIR}"

  info "备份当前版本 → ${BACKUP_DIR}/admin-${ts}.tar.gz"
  if [[ -d "${APP_ROOT}/admin" && -n "$(ls -A "${APP_ROOT}/admin" 2>/dev/null)" ]]; then
    tar -czf "${BACKUP_DIR}/admin-${ts}.tar.gz" -C "${APP_ROOT}/admin" .
  else
    warn "当前 admin 目录为空，跳过备份"
  fi

  # 保留最近 N 份
  ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

  info "替换文件"
  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${admin_tar}" -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}/admin"

  if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
    restorecon -Rv "${APP_ROOT}/admin" >/dev/null || true
  fi

  info "重载 Nginx"
  nginx -t && systemctl reload nginx

  do_verify
  info "更新完成。如需回滚：./deploy.sh rollback"
}

# ── 回滚 ───────────────────────────────────────────────────
do_rollback() {
  need_root
  local latest
  latest=$(ls -1t "${BACKUP_DIR}"/admin-*.tar.gz 2>/dev/null | head -1 || true)
  [[ -n "${latest}" ]] || { error "没有可用备份"; exit 1; }

  warn "即将回滚到：${latest}"
  read -r -p "确认继续？(yes/N) " reply
  [[ "${reply}" == "yes" ]] || { info "已取消"; exit 0; }

  find "${APP_ROOT}/admin" -mindepth 1 -delete
  tar -xzf "${latest}" -C "${APP_ROOT}/admin"

  local nginx_user="nginx"
  id "${nginx_user}" >/dev/null 2>&1 || nginx_user="www-data"
  chown -R "${nginx_user}:${nginx_user}" "${APP_ROOT}/admin"

  nginx -t && systemctl reload nginx
  info "回滚完成"
  do_verify
}

# ── 验证 ───────────────────────────────────────────────────
do_verify() {
  local host="127.0.0.1"
  info "验证..."
  sleep 1

  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/screen/" || echo "000")
  [[ "${code}" == "200" ]] && info "大屏首页        HTTP ${code} ✓" || warn "大屏首页        HTTP ${code} ✗"

  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/admin/" || echo "000")
  [[ "${code}" == "200" ]] && info "后台首页        HTTP ${code} ✓" || warn "后台首页        HTTP ${code} ✗"

  # 最关键的一项：history 回退
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/admin/system/user" || echo "000")
  if [[ "${code}" == "200" ]]; then
    info "后台子路由回退  HTTP ${code} ✓"
  else
    error "后台子路由回退  HTTP ${code} ✗  → 检查 try_files 配置！"
  fi

  code=$(curl -s -o /dev/null -w "%{http_code}" "http://${host}/api/health" || echo "000")
  [[ "${code}" == "200" ]] && info "后端 API        HTTP ${code} ✓" || warn "后端 API        HTTP ${code}（后端可能未启动）"
}

do_status() {
  echo "── Nginx ──"
  systemctl is-active nginx && nginx -t 2>&1 | tail -1
  echo "── 目录 ──"
  du -sh "${APP_ROOT}/screen" "${APP_ROOT}/admin" 2>/dev/null || true
  echo "── 备份 ──"
  ls -lht "${BACKUP_DIR}" 2>/dev/null | head -6 || echo "（无备份）"
}

case "${1:-}" in
  install)  shift; do_install  "${@:-}";;
  update)   shift; do_update   "${@:-}";;
  rollback) do_rollback;;
  status)   do_status;;
  *)
    cat <<'USAGE'
能源产效管理系统 · 部署工具

用法：
  ./deploy.sh install <大屏tar包> <后台tar包>   首次安装
  ./deploy.sh update  <后台tar包>               更新后台（自动备份）
  ./deploy.sh rollback                          回滚到最近一次备份
  ./deploy.sh status                            查看当前状态
USAGE
    exit 1
    ;;
esac
```

### 5.2 使用方式

```bash
sudo chmod +x deploy.sh

# 首次安装
sudo ./deploy.sh install screen-dist.tar.gz admin-dist.tar.gz

# 更新后台
sudo ./deploy.sh update admin-dist.tar.gz

# 出问题回滚
sudo ./deploy.sh rollback

# 看状态
sudo ./deploy.sh status
```

### 5.3 systemd 定时备份（可选）

```ini
# /etc/systemd/system/eep-backup.service
[Unit]
Description=EEP 每日备份

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'ts=$(date +\%Y\%m\%d); tar -czf /opt/eep/_backup/daily-$ts.tar.gz -C /opt/eep/admin . && ls -1t /opt/eep/_backup/daily-*.tar.gz | tail -n +8 | xargs -r rm -f'
```

```ini
# /etc/systemd/system/eep-backup.timer
[Unit]
Description=EEP 每日备份（每日 02:00）

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now eep-backup.timer
systemctl list-timers eep-backup.timer
```

---

## 六、HTTPS 配置（可选）

内网若已申请证书（或自建 CA）：

```nginx
server {
    listen 443 ssl;
    http2  on;
    server_name eep.plant01.local;

    ssl_certificate     /etc/nginx/ssl/eep.crt;
    ssl_certificate_key /etc/nginx/ssl/eep.key;

    ssl_protocols             TLSv1.2 TLSv1.3;
    ssl_ciphers               ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache         shared:SSL:10m;
    ssl_session_timeout       10m;

    add_header Strict-Transport-Security "max-age=31536000" always;

    # ... 与 HTTP 站点相同的 location 配置 ...
}

server {
    listen 80;
    server_name eep.plant01.local;
    return 301 https://$host$request_uri;
}
```

> **大屏特别提醒**：若大屏页面用 HTTPS 访问，而视频转流网关是 HTTP，
> 浏览器会以「混合内容」为由**拦截视频**。两个办法：
> ① 网关也上 HTTPS；② 全部走 HTTP（纯内网推荐）。
> 内网部署建议**统一用 HTTP**，避免这个坑。

---

## 七、常见问题

### Q1. 后台刷新子路由 404

`try_files $uri $uri/ /admin/index.html;` 没写对，或 `alias` 路径拼接错误。
**自检命令**：

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1/admin/system/user
# 期望 200；返回 404 就是回退没生效
```

### Q2. 所有页面 403 Forbidden

三种可能：
① SELinux 标签不对（见 §3.4）→ `restorecon -Rv /opt/eep`；
② 目录权限不足 → `chown -R nginx:nginx /opt/eep && chmod -R 755 /opt/eep`；
③ Nginx 进程用户与实际目录属主不匹配 → `ps aux | grep nginx` 看运行用户。

### Q3. 大屏页面正常，但视频播不了

先确认 `videoConfig.channels[*].flv` 是否已填网关地址；
其次检查 `/live/` 代理是否关了缓冲：

```bash
curl -sI http://127.0.0.1/live/a1.flv | head -5
```

如果画面几秒后中断，基本都是 `proxy_buffering` 没关或 `proxy_read_timeout` 太短。

### Q4. 更新后页面还是旧内容

浏览器强缓存了 `index.html`。本配置已对 `index.html` 加 `no-cache`；
若仍出现，检查是否有前置 CDN 或代理缓存。用户侧强制刷新 `Ctrl + F5` 可立即验证。

### Q5. `nginx -t` 报 `duplicate location`

同一个 `server` 块里定义了两次相同 `location`。检查是否有多个 `conf.d/*.conf` 都配了 `/` 或 `/api/`。

```bash
grep -rn "location /" /etc/nginx/conf.d/ /etc/nginx/nginx.conf
```

### Q6. 大屏挂机几天后内存持续增长

浏览器侧的问题（7×24 常亮页面）。本项目已做的防护：
① 图表实例在组件卸载时 `dispose()`；② 时钟每秒只改文本节点，不重绘整屏。
若仍增长，用浏览器任务管理器观察，必要时给大屏加**每日凌晨自动刷新**（可在 kiosk 模式脚本里定时 `location.reload()`）。

---

## 八、上线检查表

### 部署前

- [ ] 后台 `vite.config.ts` 的 `base` 与部署路径一致
- [ ] `pnpm build` 零报错，`pnpm typecheck` 零报错
- [ ] 产物包已校验完整性（`tar -tzf` 能列出文件）
- [ ] 已确认服务器磁盘可用空间 > 1GB

### 部署中

- [ ] Nginx 配置通过 `nginx -t`
- [ ] SELinux 标签已处理（如启用）
- [ ] 防火墙已按内网网段放行
- [ ] 后端服务健康检查通过

### 部署后

- [ ] 大屏可访问、图表正常、快捷键生效
- [ ] 后台可登录、21 个菜单逐个点开
- [ ] **刷新后台子路由不 404**
- [ ] 视频网关连通（如已配置）
- [ ] 日志轮转生效，日志目录有写入
- [ ] 备份任务已启用（timer / cron）
- [ ] 回滚流程已实测一次

### 安全

- [ ] 已接入统一身份认证（SSO），移除本地虚拟登录
- [ ] API 全部鉴权，白名单接口已评审
- [ ] CORS 白名单已配置，未使用 `*`
- [ ] 生产密钥未写入代码库或镜像
- [ ] 第三方依赖许可证已确认（无 GPL 强传染性）
- [ ] 每月依赖 CVE 巡检已排期
