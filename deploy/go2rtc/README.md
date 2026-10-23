# go2rtc 视频网关 · 部署与排障

> 现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过，本文档把该方案固化到仓库，
> 供后续产线摄像头接入与故障排查参照。

---

## 一、为什么需要它

**浏览器不能直接播放 `rtsp://`。** 这不是前端能力问题，是浏览器协议支持问题：

| 协议 | 浏览器原生支持 | 说明 |
| :--- | :--- | :--- |
| `rtsp://` | ❌ **不支持** | 摄像头出厂默认协议，必须转码/转协议 |
| HLS (m3u8) | 部分（仅 Safari） | 其他浏览器需 hls.js |
| HTTP-FLV | ❌ | 需 flv.js 解封装 |
| MSE (fMP4/WS) | ✅（需 MSE API） | Chromium 内核均支持 |
| WebRTC | ✅ | 需要信令与额外端口 |
| MJPEG | ✅（用 `<img>` 即可） | 最土但最稳 |

go2rtc 同时提供上述全部输出，因此用它做转协议网关，
前端只需按优先级自动降级即可，不需要任何插件。

---

## 二、快速部署（Windows）

现场即在用这套。

```powershell
# 1. 下载 go2rtc（Windows amd64）
#    https://github.com/AlexxIT/go2rtc/releases
#    解压到 C:\go2rtc

# 2. 放置配置
Copy-Item deploy\go2rtc\go2rtc.example.yaml C:\go2rtc\go2rtc.yaml
# 然后编辑 go2rtc.yaml，把 streams 下的摄像头地址改成实际值

# 3. 前台试跑，确认能拉到流
cd C:\go2rtc
.\go2rtc.exe

# 4. 浏览器验证（这三步能过，说明网关没问题）
#    http://127.0.0.1:1984/                                  go2rtc 首页
#    http://127.0.0.1:1984/api/streams                       流列表
#    http://127.0.0.1:1984/stream.html?src=my_camera         能出画面
#      ↑ 不要加 &mode=mse：mode 是白名单，只写 mse 就失去自动降级能力

# 5. 注册为 Windows 服务（开机自启 + 崩溃自动重启）
powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1
```

## 三、快速部署（Linux）

```bash
# 1. 下载
wget https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64
chmod +x go2rtc_linux_amd64 && sudo mv go2rtc_linux_amd64 /usr/local/bin/go2rtc

# 2. 配置
sudo mkdir -p /etc/go2rtc
sudo cp deploy/go2rtc/go2rtc.example.yaml /etc/go2rtc/go2rtc.yaml
sudo vi /etc/go2rtc/go2rtc.yaml

# 3. systemd 单元
sudo tee /etc/systemd/system/go2rtc.service > /dev/null <<'EOF'
[Unit]
Description=go2rtc streaming gateway
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/local/bin/go2rtc -config /etc/go2rtc/go2rtc.yaml
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now go2rtc
sudo systemctl status go2rtc
```

---

## 四、前后端如何配合

```
摄像头 (RTSP)
    │  rtsp://admin:***@10.113.1.118:554/Streaming/Channels/102
    ▼
go2rtc 网关（10.113.1.61:1984）
    │  转成 hls / mjpeg / mse / webrtc
    ▼
浏览器（大屏 / 后台）—— 直接连网关，不经 Django 后端
```

**前端配置位置：** `frontend/packages/shared/src/config.ts`

```ts
export const videoConfig = {
  enabled: true,
  provider: 'go2rtc',                                  // 'go2rtc' | 'custom' | 'off'
  prefer: ['mse', 'hls', 'mjpeg'],                     // 降级顺序：mse 首选，hls 可自证兜底
  go2rtc: {
    base: 'http://10.113.1.61:1984',                   // ← 改这里
    apiPort: 1984,
    webrtcPort: 8555
  },
  channels: {
    W103: { channel: 'CH01', stream: 'my_camera',        // ← 必须与 go2rtc.yaml 的 key 一致
          rtsp: 'rtsp://...', resolution: '1920×1080', fps: 25, codec: 'H.264' },
    W101: { channel: 'CH02', stream: 'my_camera', rtsp: '', resolution: '1920×1080',
          fps: 25, codec: 'H.264', enabled: false }    // ← 摄像头未到位时置 false
  },
  // ...
};
```

**为什么视频不经过后端：** 视频是长连接高码流，走 Django 会让后端成为瓶颈。
浏览器直连网关即可；若需要统一鉴权或收敛端口，再用 `deploy/nginx/eep.conf` 的 `/live/` 反代。

---

## 五、协议优先级：mse → hls → mjpeg（v1.1.1 现场实测后确定）

| 协议 | 延迟 | 需要的端口 | 前端实现方式 | 能否校验出画 | 评价 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **mse** | 0.5—2s | 仅 1984 | iframe 载入内置播放页 | ❌ 跨域不可观测 | **大屏默认首选**：延迟最低、网关不转码；现场大屏实测画面正常 |
| **hls** | 约 1—3s（0.5s 切片） | 仅 1984 | hls.js 直接拉流 | ✅ `videoWidth > 0` | **紧跟 mse 的兜底**：唯一能自证有画面的协议，mse 万一不出画它顶上 |
| **mjpeg** | 1—2s | 仅 1984 | `<img>` 直接拉流 | ✅ 收到真实帧才触发 `load` | 最后兜底；但**依赖 ffmpeg**，现场当前不可用（见第五·补节） |
| webrtc | <0.5s | 1984 + 8555(UDP/TCP) | iframe 载入 `/webrtc.html` | ❌ 跨域不可观测 | 延迟最低，但受 NAT 影响，要先放开 8555；仅作排查项 |

**为什么不默认用 mse（v1.1.1 的调整，见 `docs/02` 的 B-207 / ADR-008）：**

mse / webrtc 的播放状态对父页面**完全不可观测**：它们由 go2rtc 内置播放页承载在
跨域 iframe 里，父页面只能拿到 `iframe.onload` —— 那只能证明「网页加载成功」，
不能证明「有画面」。一旦出现「只有声音没有画面」，降级链会误判成功并卡死在第一档，
后面的 hls / mjpeg 永远轮不到 —— v1.1.1 排查时正是它挡住了真正的原因
（那次的实际根因是**视频容器高度被算成 0**，见 B-209，与协议无关）。

> 代价：延迟从 0.5—2s 变成约 1—3s（go2rtc 的 HLS 切片是 0.5s，并不慢）。
> 若现场确认大屏切到 mse 也能出画面（单线页按 **V** 键循环切换链路），
> 把 `prefer` 改成 `['mse', 'mjpeg']` 即可拿回低延迟。

**mse / webrtc 走 iframe 而不是前端自己实现的原因：**
MSE 需要把 WebSocket 收到的 fMP4 分片喂给 `SourceBuffer`，
而 `MediaSource.isTypeSupported('video/mp4; codecs="avc1.64001f,mp4a.40.2"')`
里的 codec 串随摄像头的编码档位（baseline/main/high）而变，
写死会在某些型号上直接黑屏。交给 go2rtc 自带脚本判断最稳。

> ⚠ `stream.html?src=X&mode=mse` 的 `mode` 是**白名单**语义：go2rtc 的 `video-stream.js`
> 只认 `mode` 里列出的协议，只写 `mse` 就等于「MSE 不可用则彻底不播」，不会自动往后降级。

---

## 五·补、ffmpeg 依赖（抓图 / MJPEG 必需）

go2rtc 把 H.264 转 JPEG 需要调用 **ffmpeg**。**现场网关当前未安装 ffmpeg**，实测：

```bash
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" "http://<网关IP>:1984/api/stream.mjpeg?src=my_camera"
# 200 0        ← 空响应，等于拉不到画面
curl -s "http://<网关IP>:1984/api/frame.jpeg?src=my_camera"
# exec: "ffmpeg": executable file not found in %PATH%   （HTTP 500）
```

| 能力 | 是否依赖 ffmpeg | 现场状态 |
| :--- | :--- | :--- |
| HLS（大屏首选） | ❌ 不依赖 | ✅ 可用（实测主列表返回 `CODECS="avc1.4D0029"`） |
| MSE / WebRTC（后台预览） | ❌ 不依赖 | ✅ 可用 |
| MJPEG（大屏兜底） | ✅ 依赖 | ❌ 不可用，前端会报「网关需安装 ffmpeg」 |
| 抓图 `/api/frame.jpeg` | ✅ 依赖 | ❌ 不可用（后台「抓图」按钮返回 500） |

安装方式：下载 ffmpeg 的 Windows 构建包，解压后**把 `bin` 目录加入系统 `PATH`**
（或直接放在 go2rtc.exe 同级目录），重新注册服务：

```powershell
# 验证
ffmpeg -version
# 重启网关服务（NSSM 方式部署时）
powershell -ExecutionPolicy Bypass -File deploy\windows\nssm-install.ps1 -Action restart
```

---

## 六、常见问题

### Q1. 页面显示「所有协议均无法播放」

按提示里的「失败记录」逐项排查：

```bash
# ① 网关是否在跑
curl http://<网关IP>:1984/api/streams

# ② 流名对不对（最常见）
#    返回的 JSON 顶层 key 就是可用流名，必须与 config.ts 的 stream 完全一致
curl -s http://<网关IP>:1984/api/streams

# ③ 网关能不能拉到摄像头
#    看 go2rtc 控制台日志，会有 [rtsp] 连接失败的具体原因
```

### Q2. 后台「视频监控」页显示「流名不存在」

前端配的 `stream` 与 `go2rtc.yaml` 里的 key 不一致。
注意大小写敏感（`my_camera` ≠ `My_Camera`）。

### Q3. 画面几秒后中断

如果走的是 Nginx 反代的 `/live/`，是代理缓冲问题：

```nginx
proxy_buffering          off;      # 必须关
proxy_read_timeout       3600s;    # 默认 60s 会掐断长连接
chunked_transfer_encoding off;
```

若浏览器直连网关（默认方式）则不会遇到这个问题。

### Q4. 多路同时拉流失败

海康/大华摄像头默认并发路数常见 **4—6 路**。
每一路流被一个播放端占用一份并发。go2rtc 默认按需拉流（无人观看自动断开），
但要避免大屏与后台「视频监控」页长时间同时预览同一路。

减少占用的办法：
- 大屏只在**打开单线详情时**才播放（本项目已是此设计）；
- 用**子码流**而非主码流（`Channels/102` 而非 `/101`），带宽与并发压力都小得多；
- 网关配置里限制 `streams` 数量。

### Q5. 摄像头地址怎么写

| 品牌 | 主码流 | 子码流 |
| :--- | :--- | :--- |
| 海康 | `rtsp://用户:密码@IP:554/Streaming/Channels/101` | `rtsp://用户:密码@IP:554/Streaming/Channels/102` |
| 大华 | `rtsp://用户:密码@IP:554/cam/realmonitor?channel=1&subtype=0` | `...&subtype=1` |

> 多通道录像机：海康的通道 2 是 `201`/`202`；大华是 `channel=2`。

### Q6. 大屏用 HTTPS 时视频不出来

浏览器会以**混合内容（Mixed Content）**为由拦截 HTTP 视频流。
内网部署建议统一用 **HTTP**，不要只给大屏上 HTTPS。

### Q7. 改了 `go2rtc.yaml` 不生效

需要重启网关：

```powershell
powershell -File deploy\windows\nssm-install.ps1 -Action restart
```

```bash
sudo systemctl restart go2rtc
```

---

## 七、新增一路摄像头的完整步骤

以二线（W101）为例：

1. **网关侧** —— 编辑 `go2rtc.yaml`，在 `streams` 下新增：

   ```yaml
   streams:
     my_camera:
       - rtsp://admin:密码@10.113.1.118:554/Streaming/Channels/102
     a2:                                              # ← 新增
       - rtsp://admin:密码@10.113.1.119:554/Streaming/Channels/102
   ```

   重启网关，用 `http://<网关IP>:1984/api/streams` 确认 `a2` 已出现。

2. **前端侧** —— 编辑 `frontend/packages/shared/src/config.ts`：

   ```ts
   W101: { channel: 'CH02', stream: 'a2', rtsp: 'rtsp://admin:密码@10.113.1.119:554/Streaming/Channels/102',
         resolution: '1920×1080', fps: 25, codec: 'H.264', enabled: true },   // ← 由 false 改 true
   ```

3. **验证** —— 重新构建前端并部署，打开大屏单线详情选二线（W101），右上角应显示 `HLS`（或 `MJPEG`）；
   后台「视频监控」页点「检测网关」，W101 行的「配置校验」应变为「正常」。

> 也可直接在后台「视频监控」页点「打开预览」先验证网关侧是否正常，
> 那里用的是与大屏完全相同的播放链路。
