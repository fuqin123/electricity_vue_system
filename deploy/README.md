# deploy —— 部署资源

> 本目录存放**可直接使用的部署配置文件与脚本**。
> `docs/03-版本部署-Windows版.md` / `docs/04-版本部署-Linux版.md` 负责讲清「为什么这么做」，
> 本目录的**文件是唯一真值来源** —— 改配置改这里，不要在文档里改散落的代码块。

---

## 目录结构

```text
deploy/
├─ README.md                     # 本文件
├─ go2rtc/                       # ★ 视频转流网关（浏览器监控的核心）
│  ├─ go2rtc.example.yaml        # 配置模板
│  └─ README.md                  # 部署与排障说明
├─ nginx/
│  └─ eep.conf                   # Linux 站点配置（大屏 + 后台 + API + 视频反代）
├─ windows/
│  ├─ web.config.admin           # IIS：后台 history 路由回退（关键）
│  ├─ web.config.screen          # IIS：大屏静态缓存
│  └─ nssm-install.ps1           # 把 Nginx / go2rtc 注册为 Windows 服务
├─ linux/
│  ├─ deploy.sh                  # 一键部署 / 更新 / 回滚 / 状态
│  ├─ eep-backend.service        # 后端 systemd 单元
│  ├─ logrotate.eep              # 日志轮转（保留 30 天 + 按大小滚动）
│  └─ daily-check.sh             # 每日巡检脚本
└─ docker/                       # 全栈容器化（详见 docker/README.md）
   ├─ Dockerfile                 # 前端镜像（大屏 + 后台 → Nginx）
   ├─ Dockerfile.backend         # 后端镜像（Django + gunicorn，采集容器复用）
   ├─ entrypoint.backend.sh      # 等库 → 建库 → migrate → seed → exec
   ├─ docker-compose.yml         # frontend + backend + collector + rollup + mysql（+ 可选 stream）
   ├─ nginx.default.conf         # 容器内 Nginx 配置（运行时解析上游，后端挂了前端不崩）
   ├─ .env.example               # 口令与参数模板
   ├─ settings/                  # 现场真实配置挂载点（不入库）
   ├─ build-offline-bundle.sh    # 构建机：出镜像 + docker save → 离线压缩包
   ├─ load-and-run.sh            # 目标机：导镜像 + 起服务 + 等健康检查
   └─ README.md                  # 容器化部署说明与排障对照表
```

---

## 部署形态速查

| 场景 | 用哪些文件 | 参考文档 |
| :--- | :--- | :--- |
| **Linux + Nginx**（推荐） | `nginx/eep.conf`、`linux/*` | `docs/04-版本部署-Linux版.md` 方案 A |
| **Linux + Docker** | `docker/*` | `docs/04-版本部署-Linux版.md` 方案 B |
| **Linux 一键部署** | `linux/deploy.sh` | `docs/04-版本部署-Linux版.md` 方案 C |
| **Windows + IIS** | `windows/web.config.*`、`windows/nssm-install.ps1` | `docs/03-版本部署-Windows版.md` 方案 A |
| **Windows + Nginx** | `nginx/eep.conf`、`windows/nssm-install.ps1` | `docs/03-版本部署-Windows版.md` 方案 B |
| **视频监控（所有平台）** | `go2rtc/go2rtc.example.yaml` | `go2rtc/README.md` |

---

## 三条部署红线

1. **后台必须配 history 回退**
   `apps/admin` 用 history 路由。不配 `try_files $uri $uri/ /admin/index.html`（Linux）
   或 `web.config`（IIS），表现是「首页能开、刷新任意子路由就 404」。
   **自检命令**：

   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1/admin/system/user
   # 期望 200
   ```

2. **后台部署到子路径时必须同步改 `base`**
   `frontend/apps/admin/vite.config.ts` 的 `base` 默认为 `/`。
   部署到 `/admin/` 就必须改成 `base: '/admin/'` 再构建，且 Nginx `alias` 两侧都要有尾斜杠。
   **Docker 形态不用改源码**：`docker/Dockerfile` 在构建时用 CLI 覆盖
   （`vite build --base=/admin/`），源码里的 `base: '/'` 保留给「后台放站点根目录」
   那种形态。直接把 `base` 改成 `/admin/` 会让宿主机部署（`linux/deploy.sh`）那条路断掉。

3. **`index.html` 绝不缓存，带哈希的 assets 长期缓存**
   反过来配会出现「发版后用户还在跑旧版本」或「每次访问都重新下载 1MB JS」。

---

## 快速使用

### Linux 一键部署

```bash
cd deploy/linux
chmod +x deploy.sh daily-check.sh

# 首次安装（先在开发机执行 pnpm build 并打包）
sudo ./deploy.sh install screen-dist.tar.gz admin-dist.tar.gz

# 更新后台
sudo ./deploy.sh update admin-dist.tar.gz

# 出问题回滚
sudo ./deploy.sh rollback

# 查看状态
sudo ./deploy.sh status
```

### Docker（全栈，一条命令起五个容器）

```bash
cd deploy/docker
cp .env.example .env                                   # 填口令与密钥
mkdir -p settings && cp ../../backend/config/settings.example.yaml settings/settings.local.yaml
docker compose up -d --build
```

目标机不能联网时，在构建机上打包再拷过去：

```bash
./build-offline-bundle.sh                              # → dist/eep-docker-bundle-*.tar.gz
# 拷到目标机：tar xzf 包 && cd */runtime && ./load-and-run.sh
```

服务组成、部署后验证清单（8 步 curl）、排障对照表见 `docker/README.md`。

### 挂载配置文件

```bash
# Nginx
sudo cp deploy/nginx/eep.conf /etc/nginx/conf.d/eep.conf
sudo nginx -t && sudo systemctl reload nginx

# 后端 systemd
sudo cp deploy/linux/eep-backend.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now eep-backend

# 日志轮转
sudo cp deploy/linux/logrotate.eep /etc/logrotate.d/eep

# Windows：Nginx + go2rtc 注册为服务
powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1
```
