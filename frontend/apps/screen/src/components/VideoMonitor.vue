<script setup lang="ts">
/**
 * VideoMonitor —— 产线视频监控（浏览器可播）
 *
 * ⚠ 关键事实：浏览器不能直接播放 `rtsp://`。
 *   必须由流媒体网关把 RTSP 转成浏览器认识的协议。本项目采用 **go2rtc**
 *   （现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过）。
 *
 * 播放链路（按 videoConfig.prefer 顺序自动降级）：
 *   ① mse    —— iframe 载入内置播放页 `/stream.html?src=X&mode=mse`
 *               走 WebSocket + fMP4，延迟 0.5—2s，**只用网关 HTTP 端口**，最省事
 *   ② webrtc —— iframe 载入 `/webrtc.html?src=X`，延迟 <0.5s，需额外放开 8555 端口
 *   ③ hls    —— 前端用 hls.js 直接拉 `/api/stream.m3u8?src=X`，延迟 3—8s
 *   ④ mjpeg  —— `<img>` 直接拉 `/api/stream.mjpeg?src=X`，帧率低但兜底最稳
 *
 * 为什么 mse / webrtc 走内置播放页：fMP4 的封装与编解码串由 go2rtc 自带脚本处理，
 * 前端自行实现极易在 `avc1.xxxxxx` 这种 codec 串上踩坑，实测不稳定。
 *
 * 若网关是 MediaMTX / ZLMediaKit 等自建服务，把 `videoConfig.provider` 改为 `'custom'`
 * 并在 `customEndpoints` 填 flv / hls / webrtc 地址即可，本组件会自动走旧链路。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { go2rtcUrl, videoConfig, type VideoChannel, type VideoProtocol } from '@eep/shared';
import SvgIcon from './SvgIcon.vue';

const props = defineProps<{ lineId: string }>();

type State = 'live' | 'connecting' | 'offline' | 'error' | 'unconfigured';

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
  hls: 10000,
  mjpeg: 12000
};

const state = ref<State>('offline');
const placeholderTitle = ref('视频流未接入');
const placeholderLines = ref<string[]>(['等待网关配置']);
const showVideo = ref(false);
/** 当前生效的协议，用于右上角标识与现场排障 */
const activeProtocol = ref<VideoProtocol | 'flv' | null>(null);
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

/**
 * 媒体元素的显隐完全由内联样式控制。
 * 不用 v-show：内联 display 会被全局样式 `.video.is-live .video__el{display:block}` 覆盖，
 * 而全局的 `.video__el{display:none}` 又会让 v-show 恢复显示时失效，两边打架。
 */
function mediaStyle(p: VideoProtocol | 'flv') {
  return { display: showVideo.value && activeProtocol.value === p ? 'block' : 'none' };
}

/* ── 清理：切换产线与卸载时必须停掉播放器，否则会持续占用摄像头并发路数 ── */
function destroy() {
  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
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
    iframeEl.remove();
    iframeEl = null;
  }
  if (videoEl.value) {
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
  tryLog.value = [];

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

  const list = ch.prefer?.length ? ch.prefer : videoConfig.prefer;
  if (index >= list.length) {
    setPlaceholder('所有协议均无法播放', [
      `已尝试：${list.join(' → ')}`,
      `网关：${videoConfig.go2rtc.base}`,
      `流名称：${ch.stream}`,
      ...(tryLog.value.length ? [`失败记录：${tryLog.value.join('；')}`] : []),
      '请确认网关已启动，且 stream 名称与 go2rtc.yaml 中一致'
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

/** go2rtc：mse / webrtc 走内置播放页（iframe），hls / mjpeg 由前端直接拉流 */
function playGo2rtc(protocol: VideoProtocol, stream: string): Promise<boolean> {
  return new Promise((resolve) => {
    const url = go2rtcUrl(protocol, stream);
    const box = boxEl.value;
    if (!box) return resolve(false);

    const fail = (reason: string) => {
      tryLog.value.push(`${protocol}: ${reason}`);
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      resolve(false);
    };
    const succeed = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
      activeProtocol.value = protocol;
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
      ifr.onload = () => succeed();
      ifr.onerror = () => fail('播放页加载失败');
      ifr.src = url;
      box.appendChild(ifr);
      iframeEl = ifr;
      return;
    }

    if (protocol === 'hls') {
      const video = videoEl.value;
      if (!video) return fail('video 元素缺失');

      // Safari / iOS 原生支持 HLS
      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.onloadedmetadata = () => {
          void video.play().catch(() => undefined);
          succeed();
        };
        video.onerror = () => fail('原生 HLS 播放错误');
        video.src = url;
        return;
      }

      void import('hls.js')
        .then((mod) => {
          const Hls = mod.default;
          if (!Hls.isSupported()) return fail('当前浏览器不支持 MSE');
          const hls = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 2, enableWorker: true });
          hls.loadSource(url);
          hls.attachMedia(video);
          hls.on(Hls.Events.MANIFEST_PARSED, () => {
            void video.play().catch(() => undefined);
            succeed();
          });
          hls.on(Hls.Events.ERROR, (_e: unknown, d: { fatal?: boolean }) => {
            if (d?.fatal) fail('HLS 致命错误');
          });
          hlsInstance = hls;
        })
        .catch((e: Error) => fail(`hls.js 加载失败：${e.message}`));
      return;
    }

    // mjpeg：最土但最稳的兜底
    const img = imgEl.value;
    if (!img) return fail('img 元素缺失');
    img.onload = () => succeed();
    img.onerror = () => fail('MJPEG 拉流失败');
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
    timer = window.setTimeout(() => resolve(false), PROTOCOL_TIMEOUT.mse);
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
    ifr.onload = () => done(true);
    ifr.onerror = () => done(false);
    ifr.src = item.url;
    box.appendChild(ifr);
    iframeEl = ifr;
  });

  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
  if (ok) {
    activeProtocol.value = item.key;
    showVideo.value = true;
    state.value = 'live';
    return;
  }
  tryLog.value.push(`${item.key}: 播放失败或超时`);
  await playCustom(index + 1);
}

watch(
  () => props.lineId,
  () => {
    void play(0);
  },
  { immediate: true }
);

onBeforeUnmount(destroy);
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

    <video ref="videoEl" class="video__el" :style="mediaStyle('hls')" muted playsinline autoplay />
    <img ref="imgEl" class="video__img" :style="mediaStyle('mjpeg')" alt="产线实时画面" />

    <div class="video__osd">
      <span class="video__dot" :class="{ 'is-live': state === 'live', 'is-err': state === 'error' }" />
      <span>{{ STATE_TEXT[state] }}</span>
      <span v-if="activeProtocol" class="video__proto">{{ String(activeProtocol).toUpperCase() }}</span>
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
/* MJPEG 兜底用的 <img>，显隐由内联样式控制，与 <video> 互斥 */
.video__img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.video__proto {
  padding: 0 5px;
  border-radius: 3px;
  background: rgba(88, 101, 242, 0.35);
  font-size: 9px;
  letter-spacing: 0.5px;
}
</style>
