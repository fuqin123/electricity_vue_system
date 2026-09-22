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
└─ docker/
   ├─ Dockerfile                 # 前端多阶段构建
   ├─ docker-compose.yml         # 前端 + 后端 + go2rtc 编排
   └─ nginx.default.conf         # 容器内 Nginx 配置
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
