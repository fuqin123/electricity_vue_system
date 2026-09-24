/**
 * 全局可调参数 —— 改配置不改逻辑
 *
 * ⚠ 上线前请用真实历史数据回填 energyModel 与 thresholds 中的强度/阈值参数。
 */

/** 产线静态定义 */
export interface LineConfig {
  id: string;
  name: string;
  /** 额定产能 瓶/小时，用于计划产量与达成率 */
  rated: number;
  product: string;
  shift: boolean;
}

/* ══════════════════════════════════════════════════════════════
   视频监控
   ══════════════════════════════════════════════════════════════ */

/**
 * 浏览器可用的视频协议
 *
 * ⚠ 浏览器**不能**直接播放 `rtsp://`。必须由流媒体网关转成下列之一：
 *   · mse    —— WebSocket + fMP4，走 MediaSource，延迟 0.5—2s，只用网关的 HTTP 端口，**最省事且低延迟**
 *   · webrtc —— 延迟最低（<0.5s），但需要额外的 UDP/TCP 端口且受 NAT 影响
 *   · hls    —— 兼容性最好，延迟 3—8s（切片导致）
 *   · mjpeg  —— 最土但最稳，任何浏览器都能显示，带宽高、帧率低，作为最后兜底
 */
export type VideoProtocol = 'mse' | 'webrtc' | 'hls' | 'mjpeg';

/**
 * go2rtc 网关配置
 *
 * 现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证可用，配置见其 `go2rtc.yaml`：
 *   api.listen    = :1984   → HTTP API、内置播放页、MSE/HLS/MJPEG 全走这个端口
 *   webrtc.listen = :8555   → 仅 WebRTC 需要
 *   streams.<name>          → 摄像头 RTSP 源
 */
export interface Go2rtcConfig {
  enabled: boolean;
  /** 网关地址（含协议与端口），如 http://10.113.1.61:1984 */
  base: string;
  /** API / 播放页端口，默认 1984 */
  apiPort: number;
  /** WebRTC 端口，默认 8555（go2rtc.yaml 的 webrtc.listen） */
  webrtcPort: number;
}

/** 单个视频通道 */
export interface VideoChannel {
  /** 通道号，仅用于页面展示，如 CH01 */
  channel: string;
  /**
   * 在网关上的流名称 —— go2rtc.yaml 中 `streams:` 下的 key。
   * 例如 `my_camera`，对应 rtsp://.../Streaming/Channels/102
   */
  stream: string;
  /** 摄像头 RTSP 原始地址，仅留档与展示；浏览器无法直接播放 */
  rtsp: string;
  resolution: string;
  fps: number;
  codec: string;
  /** 该通道单独指定的协议顺序；留空则使用 videoConfig.prefer */
  prefer?: VideoProtocol[];
  /** 是否启用该通道（false 时大屏显示「通道已停用」） */
  enabled?: boolean;
}

/** 自建网关（MediaMTX / ZLMediaKit 等）手工填写的播放地址 */
export interface CustomEndpoints {
  flv: string;
  hls: string;
  webrtc: string;
}

/** 视频监控总配置 */
export interface VideoConfig {
  enabled: boolean;
  /** 右下角显示码流信息 */
  showMeta: boolean;
  /**
   * 网关类型：
   *   'go2rtc' —— 由本配置自动拼 URL（推荐，现场已验证）
   *   'custom' —— 手工填写 flv / hls / webrtc 地址
   *   'off'    —— 关闭视频模块，大屏只显示占位
   */
  provider: 'go2rtc' | 'custom' | 'off';
  /** 协议优先级，按顺序自动降级（仅 provider = 'go2rtc' 时生效） */
  prefer: VideoProtocol[];
  go2rtc: Go2rtcConfig;
  channels: Record<string, VideoChannel>;
  /** provider = 'custom' 时使用 */
  customEndpoints: Record<string, CustomEndpoints>;
}


export const plantConfig = {
  name: '康师傅饮料 · 长沙工厂',
  shortName: '能源产效管理系统',
  code: 'PLANT 01',
  linesName: 'A1—A6 灌装线'
};

/** 数据源配置 */
export const dataConfig = {
  /**
   * 数据源总开关：
   *   'api'  —— 走后端接口（**现场默认**，读 MySQL 里的真实采集数据）
   *   'mock' —— 全用内置演示数据（离线演示 / 后端未就绪时用）
   *
   * ⚠ 置为 'api' 时后端必须在线，否则大屏会显示演示数据
   *   （会带上明显的「后端接口不可达」提示，不会假装成真实数据）。
   */
  mode: 'api' as 'mock' | 'api',

  /**
   * 后端尚未实现的接口域 —— 这些即使 `mode = 'api'` 也继续走内置演示数据，
   * 避免一改总开关就整片页面 404 白屏。
   * 后端把这些接口实现后，从数组里删掉对应项即可，**不用改别处代码**。
   *
   * 已实现（不在本列表里）：energy.*、admin.overview / lines / gateways /
   *                          meters / quality / collect-logs /
   *                          params（系统参数，v1.4.0）/ readings（仪表读数，v1.4.0）
   */
  mockOnly: [
    'auth',
    'admin.users',
    'admin.roles',
    'admin.menus',
    'admin.dicts',
    'admin.alarms',
    'admin.logs.operation',
    'admin.logs.login',
    'admin.reports'
  ] as string[],

  api: {
    overview: '/api/energy/overview',
    line: '/api/energy/line/{lineId}',
    hourly: '/api/energy/hourly',
    meters: '/api/energy/meters',
    collectSummary: '/api/energy/collect-summary'
  },

  /** 大屏自动刷新间隔 ms */
  refreshMs: 30000,

  /**
   * 产量数据源是否已接入 —— **当前为 false**。
   *
   * 现场产量侧（灌装机计数器 / 装箱机 PLC / MES 班次报工）尚未改造，因此：
   *   · 所有产量数值一律为 **null**（界面显示 `--`），单位电耗为 null，达成率为 0；
   *   · 这个开关只影响**展示与告警**：为 false 时页面明确标注「产量未接入」，
   *     且不生成任何与产量相关的告警（低产 / 无产量 / 达产率），
   *     避免把「还没接数据」误读成「产线停产、达产率 0%」。
   *
   * 真实接口接入后置为 true 即可恢复产量口径判定，**不需要改渲染代码**。
   */
  productionEnabled: false,

  /** 产量未接入时界面上统一引用的说明文案 */
  productionNote:
    '产量数据源未接入（灌装机计数器 / 装箱机 PLC / MES 班次报工待改造），本期产量统一显示为 --'
};

/** 六条产线定义 */
export const linesConfig: LineConfig[] = [
  { id: 'A1', name: 'A1 生产线', rated: 30000, product: '冰红茶 500ml', shift: true },
  { id: 'A2', name: 'A2 生产线', rated: 30000, product: '绿茶 500ml', shift: true },
  { id: 'A3', name: 'A3 生产线', rated: 24000, product: '茉莉蜜茶 500ml', shift: true },
  { id: 'A4', name: 'A4 生产线', rated: 24000, product: '酸梅汤 500ml', shift: true },
  { id: 'A5', name: 'A5 生产线', rated: 18000, product: '纯净水 550ml', shift: true },
  { id: 'A6', name: 'A6 生产线', rated: 18000, product: '冰糖雪梨 500ml', shift: false }
];

/**
 * 能耗模型
 *
 * 分两组口径，**不要混用**：
 *
 * ① 产量接入后的正式口径（`productionEnabled = true` 时生效）：
 *      电 = 基础负荷（待机/辅机） + 产量 × 变动电耗
 *      水 = 产量 × 单位水耗；汽 = 产量 × 单位汽耗
 *    该结构保证低产时段单位电耗自然升高，符合真实产线特征。
 *
 * ② 产量未接入期间的过渡口径（当前生效）：
 *    产量侧未改造，无法按产量折算，水/汽改用**按电量比例**给出过渡估算值，
 *    仅用于占位与图表连贯性，**不可用于考核**。真实产量接入后回到 ①。
 */
export const energyModel = {
  /** 单线基础负荷 kWh/h（待机 / 辅机 / 液压） */
  baseLoadKwhPerHour: 20,
  /** 单线变动电耗 kWh/瓶（灌装 + 输送 + 在线包装） */
  variableKwhPerBottle: 0.0068,
  /** 车间水耗强度 L/瓶（含 CIP、冷却） */
  waterPerBottleL: 2.8,
  /** 车间汽耗强度 kg/瓶（含杀菌、CIP） */
  steamPerBottleKg: 0.21,
  /**
   * ⚠ 过渡口径（产量未接入期间）：按电量估算水耗 L/kWh。
   * 数值为现场量级的粗估，**未经真实历史数据标定**，仅供占位。
   */
  waterPerKwhL: 0.55,
  /** ⚠ 过渡口径：按电量估算汽耗 kg/kWh，同样未经标定 */
  steamPerKwhKg: 0.045,
  /** 单位电耗考核目标 kWh/千瓶（产量接入后才能标定，当前仅留档） */
  targetUnitKwh: 8.2
};

/** 告警阈值 */
export const thresholds = {
  /** 单位电耗上限 kWh/千瓶 */
  unitKwhHigh: 9.6,
  /** 小时产量低于额定的比例即视为低产 */
  outputLowRate: 0.75,
  /** 整点无产量视为停线 */
  zeroHour: true,
  /** 应报未报视为数据缺失 */
  missingReport: true
};

/**
 * 视频监控配置
 *
 * 现场方案：go2rtc（已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过）。
 * 大屏由前端直接拉 HLS / MJPEG，或用 <iframe> 载入 go2rtc 内置播放页。
 *
 * ⚠ 浏览器无法直接播放 rtsp://，必须由网关转协议。
 *
 * 协议优先级默认 **mse → hls → mjpeg**：
 *
 *   ① **mse** —— iframe 载入 go2rtc 内置播放页，延迟 0.5—2s，网关不做转码。
 *      现场实测（v1.1.1 修复容器 0 高之后）大屏画面正常，是当前默认首选。
 *      代价：跨域 iframe 的播放状态**父页面无法校验**（只能拿到 onload），
 *      所以特意把 hls 排在它后面 —— hls 能自证有画面，mse 万一不出画还能顶上。
 *   ② **hls** —— hls.js + 自有 `<video>`，延迟约 1—3s（go2rtc 切片 0.5s）。
 *      **可校验**：只有音频轨道时 `video.videoWidth` 恒为 0，能当场识别并继续降级。
 *   ③ **mjpeg** —— `<img>` 拉流，收到真实帧才触发 load，兜底最稳；
 *      但**依赖网关侧的 ffmpeg**（现场未装，会失败并给出明确提示）。
 *
 * ⚠ 排查视频故障时务必先看容器尺寸：`.video` 塌成 0 高时症状与「编码不支持 / 网络不通」
 *   一模一样（都有声音、都没画面）。v1.1.1 的真实根因就是这个（docs/02 B-209）。
 */
export const videoConfig: VideoConfig = {
  enabled: true,
  showMeta: true,
  provider: 'go2rtc',
  prefer: ['mse', 'hls', 'mjpeg'],

  go2rtc: {
    enabled: true,
    /** 现场验证通过的网关地址 */
    base: 'http://10.113.1.61:1984',
    apiPort: 1984,
    webrtcPort: 8555
  },

  /**
   * 通道映射：`stream` 必须与网关 `go2rtc.yaml` 中 `streams:` 下的 key 完全一致。
   * 当前现场只有一路摄像头（my_camera → 10.113.1.118 通道 102），
   * 其余产线先复用同一路，待摄像头到位后按行填写各自流名即可。
   */
  channels: {
    A1: { channel: 'CH01', stream: 'my_camera', rtsp: 'rtsp://admin:***@10.113.1.118:554/Streaming/Channels/102', resolution: '1920×1080', fps: 25, codec: 'H.264' },
    A2: { channel: 'CH02', stream: 'my_camera', rtsp: '', resolution: '1920×1080', fps: 25, codec: 'H.264', enabled: false },
    A3: { channel: 'CH03', stream: 'my_camera', rtsp: '', resolution: '1920×1080', fps: 25, codec: 'H.264', enabled: false },
    A4: { channel: 'CH04', stream: 'my_camera', rtsp: '', resolution: '1920×1080', fps: 25, codec: 'H.264', enabled: false },
    A5: { channel: 'CH05', stream: 'my_camera', rtsp: '', resolution: '1280×720', fps: 25, codec: 'H.264', enabled: false },
    A6: { channel: 'CH06', stream: 'my_camera', rtsp: '', resolution: '1280×720', fps: 25, codec: 'H.264', enabled: false }
  },

  /** provider = 'custom' 时（MediaMTX / ZLMediaKit 等）手工填写 */
  customEndpoints: {
    A1: { flv: '', hls: '', webrtc: '' },
    A2: { flv: '', hls: '', webrtc: '' },
    A3: { flv: '', hls: '', webrtc: '' },
    A4: { flv: '', hls: '', webrtc: '' },
    A5: { flv: '', hls: '', webrtc: '' },
    A6: { flv: '', hls: '', webrtc: '' }
  }
};

/**
 * 按协议拼出 go2rtc 的播放地址
 *
 * 已核对的 go2rtc 接口（api.listen 端口）：
 *   · 内置播放页    /stream.html?src=<name>[&mode=mse|webrtc|hls]
 *   · WebRTC 专用页 /webrtc.html?src=<name>
 *   · HLS           /api/stream.m3u8?src=<name>
 *   · MJPEG         /api/stream.mjpeg?src=<name>
 *   · 抓图          /api/frame.jpeg?src=<name>
 *   · 流列表        /api/streams
 *
 * mse / webrtc 走内置播放页（iframe），因为 MSE 的 fMP4 封装由 go2rtc 自带脚本处理，
 * 前端自己实现容易在编解码串上踩坑；hls / mjpeg 则由前端直接拉流。
 *
 * ⚠ mse / webrtc 的代价是「不可校验」：父页面看不到 iframe 内部有没有画面，
 *   只能靠 iframe 的 load 事件判断「网页加载成功」。
 *   mode 参数是**白名单**语义（go2rtc 的 video-stream.js 只认 mode 里列出的协议），
 *   所以 mode=mse 就是「只走 MSE，不支持就彻底不播」，不会自动往后降级。
 */
export function go2rtcUrl(protocol: VideoProtocol, stream: string): string {
  const base = videoConfig.go2rtc.base.replace(/\/+$/, '');
  const name = encodeURIComponent(stream);
  switch (protocol) {
    case 'mse':
      return `${base}/stream.html?src=${name}&mode=mse`;
    case 'webrtc':
      // webrtc.html 是 go2rtc 的专用低延迟播放页
      return `${base}/webrtc.html?src=${name}`;
    case 'hls':
      return `${base}/api/stream.m3u8?src=${name}`;
    case 'mjpeg':
      return `${base}/api/stream.mjpeg?src=${name}`;
  }
}

/** go2rtc 抓图地址，用于通道预览缩略图 */
export function go2rtcSnapshotUrl(stream: string): string {
  const base = videoConfig.go2rtc.base.replace(/\/+$/, '');
  return `${base}/api/frame.jpeg?src=${encodeURIComponent(stream)}`;
}

/** go2rtc 流列表接口，用于后台做网关连通性与通道自检 */
export function go2rtcStreamsUrl(): string {
  const base = videoConfig.go2rtc.base.replace(/\/+$/, '');
  return `${base}/api/streams`;
}


/** 图表配色（与设计稿一致，深色大屏专用） */
export const palette = {
  cyan: '#00b0f4',
  primary: '#5865f2',
  violet: '#8b7bf7',
  purple: '#b96bee',
  magenta: '#ec48bd',
  green: '#35ed7e',
  amber: '#ffb020',
  t1: '#ffffff',
  t2: '#c9d0f5',
  t3: '#8a93c8',
  t4: '#6e78b4',
  t5: '#5f6ba8',
  grid: 'rgba(255,255,255,0.07)',
  axis: '#5f6ba8'
};

/** 产线专用色带：青 → 蓝紫 → 品红，同一和弦下可区分 6 条线 */
export const lineColors: Record<string, string> = {
  A1: '#00b0f4',
  A2: '#4ea8ff',
  A3: '#5865f2',
  A4: '#8b7bf7',
  A5: '#b96bee',
  A6: '#ec48bd'
};

/** 后台管理端品牌色（源力设计系统） */
export const adminTheme = {
  primary: '#1664ff',
  primaryHover: '#0055ff',
  success: '#2a814b',
  warning: '#bd7e00',
  danger: '#d7312a',
  border: '#eaedf1',
  borderStrong: '#dde2e9',
  textStrong: '#0c0d0e',
  textRegular: '#1d2129',
  textSubtle: '#86909c',
  surface: '#ffffff',
  surfaceContainer: '#f7f9fb',
  sidebar: '#f6f8fa'
};
