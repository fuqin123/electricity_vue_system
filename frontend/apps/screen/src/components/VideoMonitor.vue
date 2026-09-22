<script setup lang="ts">
/**
 * VideoMonitor —— 产线视频监控（大屏）
 *
 * ⚠ 关键事实：浏览器不能直接播放 `rtsp://`。
 *   必须由流媒体网关把 RTSP 转成浏览器认识的协议。本项目采用 **go2rtc**
 *   （现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过）。
 *
 * ── 默认链路：mse → hls → mjpeg（v1.1.1 现场实测后确定）──────────────
 *   ① mse   —— iframe 载入 go2rtc 内置播放页 `/stream.html?src=X&mode=mse`，
 *              延迟 0.5—2s，网关不转码。现场大屏实测画面正常，故作为默认首选。
 *              ⚠ 代价：跨域 iframe 的播放状态**父页面无法校验**，
 *                所以后面必须接一个能自证的协议。
 *   ② hls   —— 前端自己持有 <video> + hls.js 拉 `/api/stream.m3u8`，延迟约 1—3s。
 *              **可校验**：只有音频轨道时 `video.videoWidth` 恒为 0，
 *              浏览器不支持该视频编码会被当场识别出来并继续降级，不会黑屏装死。
 *   ③ mjpeg —— `<img>` 拉 `/api/stream.mjpeg`，收到真实 JPEG 帧才算成功，
 *              是任何浏览器都能显示的最后兜底（依赖网关 ffmpeg，未装则快速失败并给出原因）。
 *
 * ⚠ 历史教训（v1.1.1）：大屏曾出现「只有声音、没有画面」，
 *   真正原因是**视频容器高度被算成 0**（B-209），与协议无关。
 *   排查视频故障的第一步永远是**量 `.video` 的尺寸**。
 *   另外：任何一条失败路径都必须经 fail() 留下原因（B-210/B-211），
 *   媒体元素不能靠 `display:none` 隐藏来探测（B-212），监听要先于 load 注册（B-213）。
 *
 * 若网关是 MediaMTX / ZLMediaKit 等自建服务，把 `videoConfig.provider` 改为 `'custom'`
 * 并在 `customEndpoints` 填 flv / hls / webrtc 地址即可，本组件会自动走旧链路。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { go2rtcUrl, videoConfig, type VideoChannel, type VideoProtocol } from '@eep/shared';
import { useToast } from '@/composables/useClock';
import SvgIcon from './SvgIcon.vue';

const props = defineProps<{ lineId: string }>();

type State = 'live' | 'connecting' | 'offline' | 'error' | 'unconfigured';
/** 实际用于播放的媒体槽位，比协议多一个 flv（自建网关走 flv.js） */
type Slot = VideoProtocol | 'flv';

const STATE_TEXT: Record<State, string> = {
  live: '实时',
  connecting: '连接中',
  offline: '离线',
  error: '异常',
  unconfigured: '未接入'
};

/** 每种协议等待多久没出画面就换下一种 */
const PROTOCOL_TIMEOUT: Record<VideoProtocol, number> = {
  mse: 8000,
  webrtc: 10000,
  hls: 15000,
  mjpeg: 15000
};

/* ── 可切换链路（大屏按 V 键循环，存 localStorage）─────────────────
 * v1.1.1 现场实测后调整：**mse 排在第一位**（延迟最低、网关不转码，且现场大屏上
 * 画面正常 —— 之前「有声无画」的根因是容器 0 高，见 B-209，与协议无关）。
 * 后面接 hls：它是唯一能**自证有画面**的协议（`videoWidth > 0`），
 * mse 万一不出画面时还能顶上来。mjpeg 垫底（依赖网关 ffmpeg）。
 */
const CHAIN_KEY = 'eep.video.chain';
const PRESETS: ReadonlyArray<{ key: string; label: string; list: readonly VideoProtocol[] }> = [
  { key: 'mse', label: 'MSE → HLS → MJPEG（推荐）', list: ['mse', 'hls', 'mjpeg'] },
  { key: 'hls', label: 'HLS → MJPEG（仅用可校验协议）', list: ['hls', 'mjpeg'] },
  { key: 'webrtc', label: 'WebRTC → MJPEG', list: ['webrtc', 'mjpeg'] },
  { key: 'mjpeg', label: '仅 MJPEG（最稳）', list: ['mjpeg'] }
];

function loadPreset(): number {
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(CHAIN_KEY);
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略 */
  }
  const i = PRESETS.findIndex((p) => p.key === saved);
  return i >= 0 ? i : 0;
}

const preset = ref(loadPreset());
const { toast } = useToast();

const state = ref<State>('offline');
const placeholderTitle = ref('视频流未接入');
const placeholderLines = ref<string[]>(['等待网关配置']);
const showVideo = ref(false);
/** 当前生效的协议，用于右上角标识与现场排障 */
const activeProtocol = ref<Slot | null>(null);
/** 当前协议是否「无法校验画面」（iframe 类），大屏上会明确标出来 */
const unverified = ref(false);
/** 逐个协议的失败记录，全部失败时展示给现场 */
const tryLog = ref<string[]>([]);

const boxEl = ref<HTMLElement | null>(null);
const videoEl = ref<HTMLVideoElement | null>(null);
const imgEl = ref<HTMLImageElement | null>(null);

let iframeEl: HTMLIFrameElement | null = null;
let hlsInstance: { destroy: () => void } | null = null;
let flvInstance: { destroy: () => void } | null = null;
let timer: number | null = null;

const channel = computed<VideoChannel | null>(() => videoConfig.channels[props.lineId] ?? null);

/** 当前实际使用的协议顺序：通道单独配置 > 大屏 V 键选择 > 全局默认 */
const chainList = computed<readonly VideoProtocol[]>(() => {
  const ch = channel.value;
  if (ch?.prefer?.length) return ch.prefer;
  return PRESETS[preset.value].list;
});

/**
 * 媒体元素的显隐用 **class + opacity**（样式见 app.scss 的 `.video__el.is-on`）。
 *
 * ⚠ 不能用 `display: none`：浏览器会跳过隐藏媒体元素的首帧解码，
 *   而「探测有没有画面」恰恰要等 loadeddata —— 等于自己把标的物藏起来，探测必然超时。
 *   也不能用 `v-show`：内联 display 会与全局 `.video.is-live` 规则互相打架。
 */
function isActive(p: Slot) {
  return showVideo.value && activeProtocol.value === p;
}

/* ── 清理：切换产线与卸载时必须停掉播放器，否则会持续占用摄像头并发路数 ── */
function destroy() {
  clearTimer();
  try {
    hlsInstance?.destroy();
  } catch {
    /* ignore */
  }
  try {
    flvInstance?.destroy();
  } catch {
    /* ignore */
  }
  hlsInstance = null;
  flvInstance = null;

  if (iframeEl) {
    // 直接 remove 不会中断 iframe 内的 WebSocket/MSE 连接，先把 src 置空
    iframeEl.src = 'about:blank';
    iframeEl.remove();
    iframeEl = null;
  }
  if (videoEl.value) {
    videoEl.value.onloadeddata = null;
    videoEl.value.onerror = null;
    try {
      videoEl.value.pause();
    } catch {
      /* ignore */
    }
    videoEl.value.removeAttribute('src');
    videoEl.value.load();
  }
  if (imgEl.value) imgEl.value.removeAttribute('src');

  showVideo.value = false;
  activeProtocol.value = null;
  unverified.value = false;
}

function clearTimer() {
  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
}

function setPlaceholder(title: string, lines: string[], st: State = 'unconfigured') {
  placeholderTitle.value = title;
  placeholderLines.value = lines;
  state.value = st;
  showVideo.value = false;
}

/* ── 入口：按优先级逐个尝试 ─────────────────────────────── */
async function play(index = 0) {
  destroy();
  // ⚠ 只在最外层清空失败记录：play() 每层递归都会重新进来，
  //   若此处无条件清空，等于每次降级都把上一个协议的失败原因抹掉，
  //   最终「失败记录」永远是空的（B-210）。
  if (index === 0) tryLog.value = [];

  const ch = channel.value;

  if (!videoConfig.enabled || videoConfig.provider === 'off') {
    setPlaceholder('视频模块已关闭', ['在 shared/config.ts → videoConfig.enabled 中开启']);
    return;
  }
  if (!ch) {
    setPlaceholder('视频通道未配置', [`在 videoConfig.channels 中补上 ${props.lineId} 的配置`]);
    return;
  }
  if (ch.enabled === false) {
    setPlaceholder('该产线通道已停用', [
      `channels.${props.lineId}.enabled = false`,
      '摄像头到位后置为 true 即可启用'
    ]);
    return;
  }

  if (videoConfig.provider === 'custom') {
    await playCustom(0);
    return;
  }

  const list = chainList.value;
  if (index >= list.length) {
    setPlaceholder('所有协议均无法播放', [
      `已尝试：${list.join(' → ')}`,
      `网关：${videoConfig.go2rtc.base}`,
      `流名称：${ch.stream}`,
      ...(tryLog.value.length ? [`失败记录：${tryLog.value.join('；')}`] : []),
      '① 确认网关已启动、stream 名称与 go2rtc.yaml 一致',
      '② 按 V 键切换播放协议，逐个试到出画面为止'
    ]);
    state.value = 'error';
    return;
  }

  const protocol = list[index];
  state.value = 'connecting';
  setPlaceholder(`正在尝试 ${protocol.toUpperCase()}…`, ['连接中，请稍候']);

  const ok = await playGo2rtc(protocol, ch.stream);
  if (ok) return;
  await play(index + 1);
}

/** go2rtc：hls / mjpeg 由前端直接拉流（可校验）；mse / webrtc 走内置播放页（不可校验） */
function playGo2rtc(protocol: VideoProtocol, stream: string): Promise<boolean> {
  return new Promise((resolve) => {
    const url = go2rtcUrl(protocol, stream);

    /**
     * ⚠ 失败必须记一笔（B-211）。
     *   过去这里写的是 `if (!box) return resolve(false)` —— 静默失败。
     *   而组件首次挂载前模板 ref 全是 null，于是「开局必静默失败一次、且失败记录空空如也」，
     *   现场只看到一个「所有协议均无法播放」而拿不到任何原因。
     *   现在的原则：**任何一条退出路径都要留下原因**。
     */
    const fail = (reason: string) => {
      tryLog.value.push(`${protocol.toUpperCase()}: ${reason}`);
      clearTimer();
      resolve(false);
    };

    const box = boxEl.value;
    if (!box) return fail('视频容器尚未挂载（组件初始化时机问题）');

    /**
     * @param verified 是否**确证**有画面。iframe 类只能拿到 load 事件，
     *   因此传 false —— 界面上会标「未校验」，不假装成功。
     */
    const succeed = (verified: boolean) => {
      clearTimer();
      activeProtocol.value = protocol;
      unverified.value = !verified;
      showVideo.value = true;
      state.value = 'live';
      resolve(true);
    };

    // 超时保护：链路被拦时常见「既不报错也不出画面」
    timer = window.setTimeout(() => fail('等待超时'), PROTOCOL_TIMEOUT[protocol]);

    if (protocol === 'mse' || protocol === 'webrtc') {
      const ifr = document.createElement('iframe');
      ifr.className = 'video__iframe';
      ifr.setAttribute('allow', 'autoplay; fullscreen; encrypted-media');
      ifr.setAttribute('referrerpolicy', 'no-referrer');
      // 去掉网关播放页在窄容器里出现的滚动条（B-214）。
      // 实测 Chromium 仍支持 scrolling="no"：不加时内部可用宽 347（滚动条占 15px），
      // 加了以后 362 全可用，且画面不做任何缩放或裁切。
      ifr.setAttribute('scrolling', 'no');
      ifr.onload = () => succeed(false);
      ifr.onerror = () => fail('播放页加载失败');
      ifr.src = url;
      box.appendChild(ifr);
      iframeEl = ifr;
      return;
    }

    if (protocol === 'hls') {
      const video = videoEl.value;
      if (!video) return fail('video 元素缺失');

      // ★ 关键校验：拿到首帧才算出画。纯音频轨道时 videoWidth 恒为 0，
      //   这条判定直接拦住「浏览器不支持该视频编码」导致的「有声无画」。
      video.onloadeddata = () => {
        if (video.videoWidth > 0 && video.videoHeight > 0) succeed(true);
        else fail('只收到音频轨道，浏览器不支持该视频编码');
      };
      // 带上 MediaError 码，现场能直接判断是哪一类问题
      // （1 中断 / 2 网络 / 3 解码失败 / 4 格式不支持）
      video.onerror = () => {
        const code = video.error?.code ?? '?';
        fail(`视频元素报错（MediaError code=${code}）`);
      };

      // Safari / iOS 原生支持 HLS
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
        void video.play().catch(() => undefined);
        return;
      }

      void import('hls.js')
        .then((mod) => {
          const Hls = mod.default;
          if (!Hls.isSupported()) return fail('当前浏览器不支持 MSE，无法播 HLS');
          const hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, enableWorker: true });
          // ⚠ 必须在 loadSource 之前挂监听：网络被拦时清单请求瞬间就失败，
          //   事件早于监听注册就会丢掉，现场只会看到一句含糊的「解码失败」（B-213）。
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            void video.play().catch(() => undefined);
          });
          hls.on(Hls.Events.ERROR, (_e: unknown, d: { fatal?: boolean; details?: string }) => {
            if (d?.fatal) fail(`HLS 致命错误 ${d.details ?? ''}`.trim());
          });
          hls.loadSource(url);
          hls.attachMedia(video);
          hlsInstance = hls;
        })
        .catch((e: Error) => fail(`hls.js 加载失败：${e.message}`));
      return;
    }

    // mjpeg：收到真实 JPEG 帧才触发 load，即「有画面」的铁证
    const img = imgEl.value;
    if (!img) return fail('img 元素缺失');
    img.onload = () => succeed(true);
    // 网关侧缺 ffmpeg 时无法把 H.264 转 JPEG，表现为空响应 → 这里会立刻失败
    img.onerror = () => fail('MJPEG 拉流失败（网关需安装 ffmpeg 才能转 JPEG）');
    img.src = url;
  });
}

/* ── 自建网关（MediaMTX / ZLMediaKit）：沿用 flv → hls → webrtc 旧链路 ── */
async function playCustom(index: number) {
  const ep = videoConfig.customEndpoints[props.lineId];
  const chain = (
    [
      { key: 'flv' as const, url: ep?.flv ?? '' },
      { key: 'hls' as const, url: ep?.hls ?? '' },
      { key: 'webrtc' as const, url: ep?.webrtc ?? '' }
    ] as const
  ).filter((x) => Boolean(x.url));

  if (!chain.length) {
    setPlaceholder('等待转流网关', [
      'provider 为 custom，但 customEndpoints.' + props.lineId + ' 未填写任何地址'
    ]);
    return;
  }
  if (index >= chain.length) {
    setPlaceholder('所有协议均无法播放', [
      `已尝试：${chain.map((c) => c.key).join(' → ')}`,
      ...(tryLog.value.length ? [`失败记录：${tryLog.value.join('；')}`] : [])
    ]);
    state.value = 'error';
    return;
  }

  const item = chain[index];
  state.value = 'connecting';

  const ok = await new Promise<boolean>((resolve) => {
    timer = window.setTimeout(() => resolve(false), PROTOCOL_TIMEOUT.hls);
    let settled = false;
    const done = (v: boolean) => {
      if (!settled) {
        settled = true;
        resolve(v);
      }
    };

    if (item.key === 'flv') {
      void import('flv.js')
        .then((mod) => {
          const flvjs = mod.default;
          const video = videoEl.value;
          if (!video || !flvjs.isSupported()) return done(false);
          const player = flvjs.createPlayer(
            { type: 'flv', url: item.url, isLive: true, hasAudio: false },
            { enableStashBuffer: false, stashInitialSize: 128, autoCleanupSourceBuffer: true }
          );
          player.attachMediaElement(video);
          player.load();
          void player.play().catch(() => undefined);
          player.on(flvjs.Events.ERROR, () => done(false));
          flvInstance = player;
          done(true);
        })
        .catch(() => done(false));
      return;
    }

    if (item.key === 'hls') {
      const video = videoEl.value;
      if (!video) return done(false);
      void import('hls.js')
        .then((mod) => {
          const Hls = mod.default;
          if (!Hls.isSupported()) return done(false);
          const hls = new Hls({ lowLatencyMode: true });
          hls.loadSource(item.url);
          hls.attachMedia(video);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            void video.play().catch(() => undefined);
            done(true);
          });
          hls.on(Hls.Events.ERROR, (_e: unknown, d: { fatal?: boolean }) => {
            if (d?.fatal) done(false);
          });
          hlsInstance = hls;
        })
        .catch(() => done(false));
      return;
    }

    const box = boxEl.value;
    if (!box) return done(false);
    const ifr = document.createElement('iframe');
    ifr.className = 'video__iframe';
    ifr.setAttribute('allow', 'autoplay; fullscreen');
    ifr.setAttribute('scrolling', 'no');
    ifr.onload = () => done(true);
    ifr.onerror = () => done(false);
    ifr.src = item.url;
    box.appendChild(ifr);
    iframeEl = ifr;
  });

  clearTimer();
  if (ok) {
    activeProtocol.value = item.key;
    unverified.value = item.key === 'webrtc';
    showVideo.value = true;
    state.value = 'live';
    return;
  }
  tryLog.value.push(`${item.key}: 播放失败或超时`);
  await playCustom(index + 1);
}

/* ── 现场排查：按 V 循环切换播放链路 ─────────────────────── */
function onKeydown(e: KeyboardEvent) {
  if (e.key !== 'v' && e.key !== 'V') return;
  const t = e.target as HTMLElement | null;
  if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;

  preset.value = (preset.value + 1) % PRESETS.length;
  const p = PRESETS[preset.value];
  try {
    window.localStorage.setItem(CHAIN_KEY, p.key);
  } catch {
    /* ignore */
  }
  toast(`视频链路：${p.label}`);
  void play(0);
}

/**
 * 只在 lineId 变化时重播。
 *
 * ⚠ 绝不能给这个 watch 加 `immediate: true`（B-211）：
 *   immediate 会在 **setup 阶段**同步触发，那时模板还没挂载，boxEl / videoEl / imgEl 全是 null，
 *   于是首次播放必然静默失败 —— 表现为「进页面就是『所有协议均无法播放』，且没有任何失败原因」。
 *   首播改到 onMounted 里做。
 */
watch(
  () => props.lineId,
  () => {
    void play(0);
  }
);

onMounted(() => {
  document.addEventListener('keydown', onKeydown);
  void play(0);
});

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown);
  destroy();
});
</script>

<template>
  <div ref="boxEl" class="video" :class="{ 'is-live': showVideo }">
    <div class="video__placeholder">
      <span class="video__icon"><SvgIcon name="camera" /></span>
      <b>{{ placeholderTitle }}</b>
      <p>
        <span v-for="(line, i) in placeholderLines" :key="i">
          {{ line }}<br v-if="i < placeholderLines.length - 1" />
        </span>
      </p>
    </div>

    <video ref="videoEl" class="video__el" :class="{ 'is-on': isActive('hls') }" muted playsinline autoplay />
    <img ref="imgEl" class="video__img" :class="{ 'is-on': isActive('mjpeg') }" alt="产线实时画面" />

    <div class="video__osd">
      <span class="video__dot" :class="{ 'is-live': state === 'live', 'is-err': state === 'error' }" />
      <span>{{ STATE_TEXT[state] }}</span>
      <span
        v-if="activeProtocol"
        class="video__proto"
        :title="
          unverified
            ? '该协议由网关内置播放页（跨域 iframe）承载，前端无法校验画面是否真的出来了'
            : '已校验：确实收到了画面帧'
        "
      >
        {{ String(activeProtocol).toUpperCase() }}
      </span>
    </div>

    <div v-if="videoConfig.showMeta && channel" class="video__meta">
      <span>{{ channel.channel }}</span>
      <span>{{ channel.resolution }}</span>
      <span>{{ channel.codec }}</span>
      <span>{{ channel.fps }}fps</span>
    </div>
  </div>
</template>

<style scoped>
/* MJPEG 兜底用的 <img>，显隐同 <video>：用 opacity 而非 display */
.video__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  z-index: 1;
  opacity: 0;
}

.video__img.is-on {
  opacity: 1;
}

.video__proto {
  padding: 0 5px;
  border-radius: 3px;
  background: rgba(88, 101, 242, 0.35);
  font-size: 9px;
  letter-spacing: 0.5px;
}

/*
 * 角标只显示协议名，不再给「不可校验」的协议加琥珀色问号：
 * 现场实测 MSE 在缩放舞台下画面正常（B-209 的根因是容器 0 高，与协议无关），
 * 默认链路就是 MSE，工作正常时再挂个警告色反而像故障。
 * 「前端无法校验画面」这层信息放在 title 提示里（见模板）。
 */
</style>
