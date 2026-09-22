# 能源产效管理系统 · 版本部署（Windows 版）

> 文档版本：v1.1.0 ｜ 最后更新：2026-09-22 ｜ 适用：Windows Server 2016+ / Windows 10/11
>
> 本文覆盖两种部署形态：
> **方案 A —— IIS 托管**（推荐，用系统自带能力，无需额外软件）
> **方案 B —— Nginx for Windows**（需要更多重写规则时更省事）

> ## ⚠ 配置文件的唯一来源是 `deploy/`
>
> 从 v1.1.0 起，所有可直接使用的配置与脚本已从本文档中抽出，落到 `deploy/` 目录：
>
> | 用途 | 文件 |
> | :--- | :--- |
> | IIS 后台配置（history 回退） | `deploy/windows/web.config.admin` |
> | IIS 大屏配置（静态缓存） | `deploy/windows/web.config.screen` |
> | Nginx 站点配置 | `deploy/nginx/eep.conf` |
> | Windows 服务注册（Nginx / go2rtc） | `deploy/windows/nssm-install.ps1` |
> | 视频网关配置 | `deploy/go2rtc/go2rtc.example.yaml` |
>
> **改配置改 `deploy/` 下的文件**，本文档中的代码块仅作讲解与示意，不保证与文件同步。
> 部署资源总览见 `deploy/README.md`。

---

## 一、部署前必读

### 1.1 部署的是静态文件，生产环境不需要 Node.js

`pnpm build` 产出的 `dist/` 是**纯静态文件**（HTML + JS + CSS + 字体）。
生产服务器上**不需要安装 Node.js / pnpm**。

有两种做法，按机房条件二选一：

| 做法 | 适用场景 |
| :--- | :--- |
| **A. 本机构建，上传产物**（推荐） | 生产服务器不出网、或不允许装 Node。开发机构建后把 `dist/` 拷过去 |
| **B. 服务器上构建** | 服务器能出网、希望 CI/CD 在服务器侧完成。需装 Node 22 + pnpm |

### 1.2 两个应用的部署形态不同（关键）

| 应用 | 路由模式 | 目录建议 | 服务端要求 |
| :--- | :--- | :--- | :--- |
| 大屏 | `hash` | `/screen` | **无需**任何 rewrite，任意静态目录可跑 |
| 后台 | `history` | `/admin` | **必须**配置「未命中路径回退到 `index.html`** |

> 后台如果不配 history 回退，表现是：首页能打开，但**刷新任意子路由就 404**。
> 这是本项目最常见的部署事故，务必先验证。

### 1.3 端口与域名规划（按现场情况调整）

| 项 | 建议值 |
| :--- | :--- |
| 站点端口 | `80`（或 `8080`） |
| 大屏访问地址 | `http://<服务器IP>/screen/` |
| 后台访问地址 | `http://<服务器IP>/admin/` |
| 后端 API | `http://<服务器IP>/api/`（反向代理到 Django，通常是 `127.0.0.1:8000`） |

---

## 二、构建产物

### 2.1 开发机构建

```powershell
cd E:\能源产效管理平台\electricity_vue_system
pnpm install
pnpm build
```

产物：

```
frontend\apps\screen\dist\      → 大屏（约 1.9 MB，含 ECharts/flv/hls）
frontend\apps\admin\dist\       → 后台（约 2.3 MB，含 Element Plus/ECharts）
```

### 2.2 若后台要部署在子路径 `/admin/`

`frontend/apps/admin/vite.config.ts` 中的 `base` 默认是 `/`。部署到子路径时**必须改**：

```ts
export default defineConfig({
  base: '/admin/',          // ← 改成实际的子路径
  // ...
});
```

改完重新 `pnpm build:admin`。**大屏不用改**（已经是 `base: './'` 相对路径）。

### 2.3 打包上传

```powershell
# 打成 zip 便于传输
Compress-Archive -Path "frontend\apps\screen\dist\*" -DestinationPath "screen-dist.zip" -Force
Compress-Archive -Path "frontend\apps\admin\dist\*"  -DestinationPath "admin-dist.zip"  -Force
```

---

## 三、方案 A：IIS 托管（推荐）

### 3.1 安装 IIS 与所需组件

以管理员身份打开 PowerShell：

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName IIS-WebServerRole, `
  IIS-WebServer, IIS-CommonHttpFeatures, IIS-StaticContent, `
  IIS-DefaultDocument, IIS-HttpErrors, IIS-HttpLogging, `
  IIS-RequestFiltering, IIS-HttpCompressionStatic, `
  IIS-WebServerManagementTools, IIS-ManagementConsole -All
```

也可以用图形界面：`控制面板 → 程序 → 启用或关闭 Windows 功能 → Internet Information Services`。

验证：浏览器打开 `http://localhost/`，看到 IIS 欢迎页即成功。

### 3.2 安装 URL Rewrite 模块（后台 history 模式必需）

从微软官网下载安装 **URL Rewrite Module 2.1**：

```
https://www.iis.net/downloads/microsoft/url-rewrite
```

> 只有**后台**需要它（history 回退）。大屏是 hash 模式，不装也能跑。

### 3.3 部署文件

```powershell
# 建目录
New-Item -ItemType Directory -Force -Path "C:\inetpub\eep\screen"
New-Item -ItemType Directory -Force -Path "C:\inetpub\eep\admin"

# 解压产物
Expand-Archive -Path "screen-dist.zip" -DestinationPath "C:\inetpub\eep\screen" -Force
Expand-Archive -Path "admin-dist.zip"  -DestinationPath "C:\inetpub\eep\admin"  -Force

# 授权 IIS 应用池账户读取
icacls "C:\inetpub\eep" /grant "IIS_IUSRS:(OI)(CI)R" /T
```

最终目录结构：

```
C:\inetpub\eep\
├─ screen\           # 大屏
│  ├─ index.html
│  └─ assets\
└─ admin\            # 后台
   ├─ index.html
   ├─ web.config     # ← 需要手工创建，见 3.5
   └─ assets\
```

### 3.4 创建两个 IIS 站点

**图形界面方式**：IIS 管理器 → 网站 → 右键「添加网站」

| 字段 | 大屏 | 后台 |
| :--- | :--- | :--- |
| 网站名称 | `eep-screen` | `eep-admin` |
| 物理路径 | `C:\inetpub\eep\screen` | `C:\inetpub\eep\admin` |
| 绑定 | `http` · 全部未分配 · `8080` | `http` · 全部未分配 · `8081` |
| 应用程序池 | 新建 `eep-screen` (.NET CLR 版本选「无托管代码」) | 新建 `eep-admin`（同） |

**PowerShell 方式**：

```powershell
Import-Module WebAdministration

New-WebAppPool -Name "eep-screen"
Set-ItemProperty "IIS:\AppPools\eep-screen" managedRuntimeVersion ""

New-WebAppPool -Name "eep-admin"
Set-ItemProperty "IIS:\AppPools\eep-admin" managedRuntimeVersion ""

New-Website -Name "eep-screen" -PhysicalPath "C:\inetpub\eep\screen" `
  -ApplicationPool "eep-screen" -Port 8080 -Force

New-Website -Name "eep-admin" -PhysicalPath "C:\inetpub\eep\admin" `
  -ApplicationPool "eep-admin" -Port 8081 -Force
```

> **应用程序池的 .NET CLR 版本必须选「无托管代码」**，这是纯静态站点，
> 挂上托管运行时反而会引入不必要的加载。

### 3.5 后台 history 回退配置（关键步骤）

在 `C:\inetpub\eep\admin\` 下新建 `web.config`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>

    <!-- ① history 模式路由回退：未命中的路径一律交给 index.html -->
    <rewrite>
      <rules>
        <rule name="Vue History Fallback" stopProcessing="true">
          <match url=".*" />
          <conditions logicalGrouping="MatchAll">
            <!-- 真实存在的文件/目录不走回退 -->
            <add input="{REQUEST_FILENAME}" matchType="IsFile" negate="true" />
            <add input="{REQUEST_FILENAME}" matchType="IsDirectory" negate="true" />
            <!-- 静态资源与 API 不走回退 -->
            <add input="{REQUEST_URI}" pattern="^/admin/assets/" negate="true" />
            <add input="{REQUEST_URI}" pattern="^/api/" negate="true" />
          </conditions>
          <action type="Rewrite" url="/admin/index.html" />
        </rule>
      </rules>
    </rewrite>

    <!-- ② 静态资源压缩 -->
    <httpCompression>
      <dynamicTypes>
        <add mimeType="application/javascript" enabled="true" />
        <add mimeType="application/json" enabled="true" />
      </dynamicTypes>
    </httpCompression>

    <!-- ③ 静态内容 MIME 类型（部分老系统没有 .mjs / .woff2） -->
    <staticContent>
      <remove fileExtension=".mjs" />
      <mimeMap fileExtension=".mjs" mimeType="application/javascript" />
      <remove fileExtension=".woff2" />
      <mimeMap fileExtension=".woff2" mimeType="font/woff2" />
      <clientCache cacheControlMode="UseMaxAge" cacheControlMaxAge="365.00:00:00" />
    </staticContent>

    <httpErrors errorMode="Detailed" />

  </system.webServer>
</configuration>
```

> **注意**：`<action url>` 与 `assets` 的排除规则里都要带上 `/admin/` 前缀，
> 这与 `vite.config.ts` 的 `base` 保持一致。若后台部署在**站点根目录**，
> 把这两处 `/admin/` 都去掉即可。

### 3.6 大屏的 web.config（可选，仅加缓存）

大屏不需要 rewrite，但建议加一段静态资源缓存，减少墙挂设备的重复加载：

在 `C:\inetpub\eep\screen\` 下新建 `web.config`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <staticContent>
      <remove fileExtension=".mjs" />
      <mimeMap fileExtension=".mjs" mimeType="application/javascript" />
      <clientCache cacheControlMode="UseMaxAge" cacheControlMaxAge="365.00:00:00" />
    </staticContent>
  </system.webServer>
</configuration>
```

### 3.7 反向代理后端 API

若后端 Django 跑在同一台机器（`127.0.0.1:8000`），需要在 IIS 上再加反向代理。

1. 安装 **Application Request Routing (ARR)** 与 **URL Rewrite**；
2. IIS 管理器 → 服务器节点 → ARR Cache → 右侧「Server Proxy Settings」→ 勾选 **Enable proxy**；
3. 在**站点根**（不是子应用）的 `web.config` 里加规则：

```xml
<rule name="API Proxy" stopProcessing="true">
  <match url="^api/(.*)" />
  <action type="Rewrite" url="http://127.0.0.1:8000/api/{R:1}" />
</rule>
```

> 更稳妥的做法是**让 Nginx / Traefik 之类专门做反代**，IIS 只负责静态文件。
> 如果现场只有 IIS，上述方案可用，但要注意 ARR 的超时设置（默认 30 秒）。

### 3.8 防火墙放行

```powershell
New-NetFirewallRule -DisplayName "EEP Screen 8080" -Direction Inbound `
  -Protocol TCP -LocalPort 8080 -Action Allow

New-NetFirewallRule -DisplayName "EEP Admin 8081" -Direction Inbound `
  -Protocol TCP -LocalPort 8081 -Action Allow
```

> 生产环境建议**只放行内网网段**：
> `-RemoteAddress 10.20.0.0/16,192.168.0.0/16`

### 3.9 验证清单

```powershell
# 站点状态
Get-Website | Where-Object { $_.Name -like "eep-*" } | Select-Object Name, State, PhysicalPath
```

逐项人工验证：

- [ ] `http://<IP>:8080/` 打开大屏总览，无白屏
- [ ] 大屏按 `2` 能进单线详情，图表正常渲染
- [ ] `http://<IP>:8081/login` 打开后台登录页
- [ ] 登录后逐个点击 21 个菜单，均能打开
- [ ] **刷新后台任意子路由（如 `/admin/system/user`）不 404** ← 最关键的一项
- [ ] 断网后大屏仍能显示（静态资源已缓存）

---

## 四、方案 B：Nginx for Windows

适用场景：需要更灵活的重写规则、或已有 Nginx 运维经验。

### 4.1 安装

下载 **nginx/Windows 稳定版**（`nginx.org/en/download.html`），解压到 `C:\nginx`。

以管理员身份运行：

```powershell
cd C:\nginx
start nginx            # 启动
nginx -s reload        # 重载配置
nginx -s stop          # 停止
tasklist /FI "IMAGENAME eq nginx.exe"   # 查进程
```

### 4.2 目录准备

```powershell
New-Item -ItemType Directory -Force -Path "C:\inetpub\eep"
Expand-Archive -Path "screen-dist.zip" -DestinationPath "C:\inetpub\eep\screen" -Force
Expand-Archive -Path "admin-dist.zip"  -DestinationPath "C:\inetpub\eep\admin"  -Force
```

### 4.3 完整配置 `C:\nginx\conf\nginx.conf`

```nginx
worker_processes  2;

events {
    worker_connections  1024;
}

http {
    include       mime.types;
    default_type  application/octet-stream;

    # 日志
    log_format  main  '$remote_addr - $remote_user [$time_local] "$request" '
                      '$status $body_bytes_sent "$http_referer" '
                      '"$http_user_agent" rt=$request_time';
    access_log  logs/access.log  main;
    error_log   logs/error.log   warn;

    sendfile        on;
    tcp_nopush      on;
    keepalive_timeout  65;

    # 静态资源压缩（大屏 JS 体积较大，收益明显）
    gzip  on;
    gzip_comp_level  6;
    gzip_min_length  1024;
    gzip_types  text/plain text/css application/javascript application/json
                application/xml image/svg+xml font/woff2;

    # 后端 Django 服务
    upstream eep_backend {
        server 127.0.0.1:8000;
        keepalive 16;
    }

    server {
        listen       80;
        server_name  _;

        # 客户端上传限制（报表导入等）
        client_max_body_size  20m;

        charset utf-8;

        # ── 大屏 ──────────────────────────────────────────
        # hash 路由，无需 rewrite
        location /screen/ {
            alias       C:/inetpub/eep/screen/;
            index       index.html;

            # 带 hash 的产物文件名可长期缓存
            location ~* /screen/assets/.*\.(js|css|woff2?|png|svg)$ {
                expires 365d;
                add_header Cache-Control "public, immutable";
                access_log off;
            }

            # index.html 绝不缓存，保证发版即生效
            location = /screen/index.html {
                add_header Cache-Control "no-cache, no-store, must-revalidate";
            }
        }

        # 兼容不带斜杠的访问
        location = /screen {
            return 301 /screen/;
        }

        # ── 后台管理 ──────────────────────────────────────
        location /admin/ {
            alias       C:/inetpub/eep/admin/;
            index       index.html;

            # ★ history 模式回退：未命中的路径交给 index.html
            try_files $uri $uri/ /admin/index.html;

            location ~* /admin/assets/.*\.(js|css|woff2?|png|svg)$ {
                expires 365d;
                add_header Cache-Control "public, immutable";
                access_log off;
            }

            location = /admin/index.html {
                add_header Cache-Control "no-cache, no-store, must-revalidate";
            }
        }

        location = /admin {
            return 301 /admin/;
        }

        # ── 后端 API 反向代理 ─────────────────────────────
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
        }

        # ── 视频转流网关（go2rtc）──────────────────────────
        # 若网关在同机 1984 端口
        location /live/ {
            proxy_pass          http://127.0.0.1:1984/;
            proxy_http_version  1.1;
            proxy_set_header    Host $host;
            # FLV 是长连接，必须放宽超时
            proxy_buffering     off;
            proxy_read_timeout  3600s;
            chunked_transfer_encoding off;
            add_header          Access-Control-Allow-Origin *;
        }

        # ── 根路径跳后台 ──────────────────────────────────
        location = / {
            return 302 /admin/;
        }
    }
}
```

### 4.4 应用配置并验证

```powershell
cd C:\nginx
nginx -t                 # 校验语法，出现 "syntax is ok" / "test is successful" 才继续
nginx -s reload
```

> **Windows 版 Nginx 的注意事项**
> - 路径分隔符要用正斜杠 `/`，或双反斜杠 `\\`；
> - 不支持 `gzip_static`、`limit_req` 等部分模块（Windows 版功能是全的，但个别第三方模块缺失）；
> - **性能弱于 Linux 版**，大屏 6 路视频并发拉流时建议改用 Linux；
> - 官方的 Win32 构建不提供 Windows 服务，需要开机自启就用下面的 NSSM。

### 4.5 注册为 Windows 服务（NSSM）

Nginx for Windows 没有自带的服务封装，用 **NSSM** 把它做成服务，实现开机自启与崩溃自动重启。

```powershell
# 下载 nssm 并解压到 C:\tools\nssm
# https://nssm.cc/download

cd C:\tools\nssm\win64

# 安装服务
.\nssm.exe install eep-nginx "C:\nginx\nginx.exe"
.\nssm.exe set eep-nginx AppDirectory "C:\nginx"

# 退出时优雅停止（发 nginx -s quit 而不是直接杀进程）
.\nssm.exe set eep-nginx AppStopMethodConsole 15000

# 崩溃自动重启（延迟 5 秒）
.\nssm.exe set eep-nginx AppExit Default Restart
.\nssm.exe set eep-nginx AppRestartDelay 5000

# 日志轮转
.\nssm.exe set eep-nginx AppStdout "C:\nginx\logs\nssm-out.log"
.\nssm.exe set eep-nginx AppStderr "C:\nginx\logs\nssm-err.log"
.\nssm.exe set eep-nginx AppRotateFiles 1
.\nssm.exe set eep-nginx AppRotateOnline 1
.\nssm.exe set eep-nginx AppRotateBytes 10485760

# 启动
.\nssm.exe start eep-nginx

# 查看状态 / 停止 / 卸载
.\nssm.exe status eep-nginx
.\nssm.exe stop   eep-nginx
.\nssm.exe remove eep-nginx confirm
```

---

## 五、Windows 部署后的日常更新流程

```powershell
# ── 1. 在开发机构建 ─────────────────────────────────
cd E:\能源产效管理平台\electricity_vue_system
pnpm build
Compress-Archive -Path "frontend\apps\admin\dist\*" -DestinationPath "admin-dist.zip" -Force

# ── 2. 上传 admin-dist.zip 到服务器（远程桌面 / 共享目录 / scp）──

# ── 3. 服务器上：备份旧版本（保留最近 5 次）────────────
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$root = "C:\inetpub\eep"
New-Item -ItemType Directory -Force -Path "$root\_backup" | Out-Null
if (Test-Path "$root\admin") {
  Compress-Archive -Path "$root\admin\*" -DestinationPath "$root\_backup\admin-$ts.zip" -Force
}

# 清理超过 5 个的旧备份
Get-ChildItem "$root\_backup\admin-*.zip" | Sort-Object LastWriteTime -Descending |
  Select-Object -Skip 5 | Remove-Item -Force

# ── 4. 替换文件（保留 web.config！）───────────────────
Copy-Item "$root\admin\web.config" "$env:TEMP\eep-web.config" -Force -ErrorAction SilentlyContinue
Remove-Item "$root\admin\*" -Recurse -Force
Expand-Archive -Path "admin-dist.zip" -DestinationPath "$root\admin" -Force
Copy-Item "$env:TEMP\eep-web.config" "$root\admin\web.config" -Force -ErrorAction SilentlyContinue

# ── 5. 回收应用池（刷新文件句柄，避免旧文件被占用）────
Restart-WebAppPool -Name "eep-admin"

# ── 6. 验证 ─────────────────────────────────────────
Start-Process "http://localhost:8081/admin/"
```

> **注意**：`Remove-Item` 前一定要先把 `web.config` 备份出来，否则 history 回退配置会被一起删掉，
> 表现为「首页能开、刷新子路由 404」。

---

## 六、回滚

```powershell
# 列出可用备份
Get-ChildItem "C:\inetpub\eep\_backup\admin-*.zip" | Sort-Object LastWriteTime -Descending

# 回滚到指定版本
$zip = "C:\inetpub\eep\_backup\admin-20260922-103000.zip"
Copy-Item "C:\inetpub\eep\admin\web.config" "$env:TEMP\eep-web.config" -Force
Remove-Item "C:\inetpub\eep\admin\*" -Recurse -Force
Expand-Archive -Path $zip -DestinationPath "C:\inetpub\eep\admin" -Force
Copy-Item "$env:TEMP\eep-web.config" "C:\inetpub\eep\admin\web.config" -Force
Restart-WebAppPool -Name "eep-admin"
```

**回滚目标时间 ≤ 5 分钟**（含验证）。建议每次发版前先把这一步的命令准备好。

---

## 七、常见问题

### Q1. 后台刷新子路由 404

history 回退没配好。检查三处：
① 是否装了 URL Rewrite 模块（IIS）；
② `web.config` 是否存在于 `admin` 目录下；
③ `<action url>` 的前缀是否与 `vite.config.ts` 的 `base` 一致。

### Q2. 页面打开是空白，控制台报 `Failed to load module script`

MIME 类型不对。IIS 默认不认识 `.mjs`，需在 `web.config` 的 `<staticContent>` 里补充映射
（本文件的 3.5 节配置已包含）。

### Q3. 大屏显示正常但图表是空白

ECharts 已打进产物，不会出现「库未加载」。多半是后端接口返回结构不符合约定。
打开开发者工具看 Network，确认 `/api/energy/overview` 返回的顶层含 `workshop` 字段。

### Q4. 视频不播放

先确认**是否已配置转流网关地址**（`frontend/packages/shared/src/config.ts` → `videoConfig.channels[*].flv`）。
RTSP 不能被浏览器直接播放，这一步不做，页面上就是「RTSP 已配置，等待转流网关」的占位。

### Q5. 应用池频繁回收导致首次访问慢

应用池默认 1740 分钟空闲回收。大屏场景建议关闭空闲回收：

```powershell
Set-ItemProperty "IIS:\AppPools\eep-screen" -Name processModel.idleTimeout -Value "00:00:00"
Set-ItemProperty "IIS:\AppPools\eep-screen" -Name recycling.periodicRestart.time -Value "00:00:00"
```

### Q6. 提示端口被占用

```powershell
netstat -ano | findstr :8080
Get-Process -Id <PID>
```

---

## 八、防火墙与安全基线

- [ ] 只放行内网网段，不对公网暴露
- [ ] 关闭 IIS 目录浏览（默认已关）
- [ ] 移除响应头中的服务器版本信息（`UrlScan` 或 `web.config` 的 `<httpProtocol><customHeaders><remove name="X-Powered-By"/>`）
- [ ] 后端 API 走 HTTPS 或限制在内网（当前方案为内网 HTTP）
- [ ] 生产环境接入统一身份认证（SSO），移除本地虚拟登录
- [ ] 定期（每月）巡检第三方依赖 CVE 公告
