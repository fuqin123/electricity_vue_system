<script setup lang="ts">
/**
 * VideoView —— 视频监控（后台侧）
 *
 * ⚠ 浏览器无法直接播放 `rtsp://`，必须由流媒体网关转协议。
 *   本项目采用 **go2rtc**（现场已在 `E:\能源产效管理平台\go2rtc_win64` 验证通过）。
 *
 * 本页承担四件事：
 *   ① 维护网关 IP / 端口（v1.7.5 起落库，不再改前端源码）
 *   ② 维护各产线摄像头的 RTSP 源与 go2rtc 流名称
 *   ③ 连接自检 —— 真实请求 go2rtc 的 /api/streams，核对通道流名是否存在
 *   ④ 通道列表 + 实时预览（iframe 载入 go2rtc 内置播放页，与大屏同一链路）
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import {
  adminApi,
  applyVideoConfig,
  go2rtcSnapshotUrl,
  go2rtcStreamsUrl,
  go2rtcUrl,
  linesConfig,
  type VideoChannelPayload,
  type VideoConfigPayload
} from '@eep/shared';
import PageHeader from '@/components/PageHeader.vue';

interface Row {
  lineId: string;
  lineName: string;
  ch: VideoChannelPayload;
}

function blankChannel(lineCode: string, lineName: string): VideoChannelPayload {
  return {
    lineCode,
    lineName,
    channel: '',
    stream: '',
    rtsp: '',
    resolution: '1920×1080',
    fps: 25,
    codec: 'H.264',
    enabled: false
  };
}

const config = ref<VideoConfigPayload>({
  enabled: true,
  gateway: { host: '', apiPort: 1984, webrtcPort: 8555 },
  channels: linesConfig.map((l) => blankChannel(l.id, l.name))
});

/** 固定按六条产线展示；后端缺哪条就用空通道补齐，保证现场可直接录入 */
const rows = computed<Row[]>(() =>
  linesConfig.map((l) => ({
    lineId: l.id,
    lineName: l.name,
    ch: config.value.channels.find((c) => c.lineCode === l.id) ?? blankChannel(l.id, l.name)
  }))
);

const gatewayBase = computed(() => {
  const g = config.value.gateway;
  return g.host ? `http://${g.host}:${g.apiPort}` : '';
});

/* ── 网关连接自检 ───────────────────────────────────────── */
const testing = ref(false);
const gatewayOk = ref<boolean | null>(null);
const gatewayMsg = ref('尚未检测');
/** 网关上实际存在的流名 */
const gatewayStreams = ref<string[]>([]);

async function checkGateway() {
  testing.value = true;
  gatewayOk.value = null;
  gatewayMsg.value = '检测中…';
  try {
    const res = await fetch(go2rtcStreamsUrl(), { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = (await res.json()) as Record<string, unknown>;
    gatewayStreams.value = Object.keys(data);
    gatewayOk.value = true;
    gatewayMsg.value = `网关可达，共 ${gatewayStreams.value.length} 路流：${gatewayStreams.value.join('、')}`;
  } catch (e) {
    gatewayOk.value = false;
    gatewayStreams.value = [];
    gatewayMsg.value = `无法连接网关（${(e as Error).message}）。请确认 go2rtc 已启动、地址与端口正确，且本机与网关网络互通。`;
  } finally {
    testing.value = false;
  }
}

/** 校验配置的流名是否真的存在于网关上 —— 这是最常见的一类配置错误 */
function streamStatus(ch: VideoChannelPayload | undefined): { text: string; type: 'success' | 'danger' | 'info' } {
  if (!ch) return { text: '未配置', type: 'info' };
  if (ch.enabled === false) return { text: '已停用', type: 'info' };
  if (gatewayOk.value === null) return { text: '未检测', type: 'info' };
  if (gatewayOk.value === false) return { text: '网关不可达', type: 'danger' };
  return gatewayStreams.value.includes(ch.stream)
    ? { text: '正常', type: 'success' }
    : { text: '流名不存在', type: 'danger' };
}

/** 只对有摄像头的通道做预览，避免 6 个空 iframe 反复重连 */
const showPreview = ref(false);
const previewRows = computed<Row[]>(() =>
  showPreview.value ? rows.value.filter((r) => r.ch.enabled !== false && r.ch.stream) : []
);

function togglePreview() {
  showPreview.value = !showPreview.value;
}

/** 打开网关抓图（新窗口），用于快速确认摄像头画面是否正常 */
function openSnapshot(stream: string) {
  window.open(go2rtcSnapshotUrl(stream), '_blank', 'noopener');
}

/* ── 读写配置（v1.7.5）───────────────────────────────────── */
const loading = ref(false);
const saving = ref(false);

async function loadConfig() {
  loading.value = true;
  try {
    const cfg = await adminApi.getVideoConfig();
    config.value = {
      ...cfg,
      channels: linesConfig.map(
        (l) => cfg.channels.find((c) => c.lineCode === l.id) ?? blankChannel(l.id, l.name)
      )
    };
    applyVideoConfig(config.value);
  } catch (e) {
    ElMessage.error(`读取视频配置失败：${(e as Error).message}`);
  } finally {
    loading.value = false;
  }
}

async function saveConfig() {
  saving.value = true;
  try {
    const payload: VideoConfigPayload = {
      ...config.value,
      gateway: { ...config.value.gateway, enabled: config.value.enabled }
    };
    const saved = await adminApi.saveVideoConfig(payload);
    config.value = {
      ...saved,
      channels: linesConfig.map(
        (l) => saved.channels.find((c) => c.lineCode === l.id) ?? blankChannel(l.id, l.name)
      )
    };
    // 让抓图 / 预览立即使用新网关地址，无需刷新页面
    applyVideoConfig(config.value);
    ElMessage.success('视频监控配置已保存');
    void checkGateway();
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`);
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  await loadConfig();
  void checkGateway();
});
</script>

<template>
  <div>
    <PageHeader>
      <template #actions>
        <el-button :loading="testing" @click="checkGateway">
          <el-icon><Refresh /></el-icon>&nbsp;检测网关
        </el-button>
        <el-button :disabled="!previewRows.length && showPreview" @click="togglePreview">
          <el-icon><VideoCamera /></el-icon>&nbsp;{{ showPreview ? '关闭预览' : '打开预览' }}
        </el-button>
        <el-button type="primary" :loading="saving" @click="saveConfig">
          <el-icon><Check /></el-icon>&nbsp;保存配置
        </el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="mb-16"
      title="链路说明：浏览器不能直接播放 rtsp://"
      description="视频源是 RTSP，必须由 go2rtc 转成浏览器认识的协议。本页可直接维护网关 IP / 端口与各产线摄像头的 RTSP 源，保存后落库；大屏刷新后按新配置取流。本页预览走 MSE（低延迟，直接载入 go2rtc 内置播放页）；大屏默认走 HLS → MJPEG，因为大屏是 transform: scale() 缩放舞台，跨域 iframe 内的视频在缩放祖先下可能不显示，且 iframe 类协议无法校验「到底有没有画面」。大屏上按 V 键可循环切换链路用于现场排查。"
    />

    <!-- ── 网关配置与自检 ───────────────────────────────── -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">转流网关</h3>
        <el-tag v-if="gatewayOk === true" type="success" size="small">在线</el-tag>
        <el-tag v-else-if="gatewayOk === false" type="danger" size="small">不可达</el-tag>
        <el-tag v-else type="info" size="small">未检测</el-tag>
      </div>

      <el-form :model="config" label-width="120px" class="gateway-form">
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="网关 IP / 主机">
              <el-input v-model="config.gateway.host" placeholder="如 10.113.1.61" clearable />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="API 端口">
              <el-input-number v-model="config.gateway.apiPort" :min="1" :max="65535" controls-position="right" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="WebRTC 端口">
              <el-input-number v-model="config.gateway.webrtcPort" :min="1" :max="65535" controls-position="right" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="启用视频">
              <el-switch v-model="config.enabled" />
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="服务地址">
              <span class="t-mono">{{ gatewayBase || '未配置' }}</span>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <el-alert
        type="info"
        :closable="false"
        class="mb-12"
        title="修改后请点右上角「保存配置」；大屏刷新页面后生效"
        description="网关 IP / 端口对应 go2rtc 的 api.listen；WebRTC 端口对应 webrtc.listen。RTSP 源填各产线摄像头的原始地址（可含账号密码），仅后台可见，不会下发给大屏。"
      />

      <el-alert
        v-if="gatewayOk !== null"
        :type="gatewayOk ? 'success' : 'error'"
        :closable="false"
        show-icon
        class="mt-12"
        :title="gatewayOk ? '网关连接正常' : '网关连接失败'"
        :description="gatewayMsg"
      />
    </div>

    <!-- ── 实时预览 ─────────────────────────────────────── -->
    <div v-if="showPreview" class="card">
      <div class="card__head">
        <h3 class="card__title">实时预览</h3>
        <span class="card__extra">此处走 go2rtc 内置播放页（MSE，低延迟）；大屏默认走 HLS</span>
      </div>
      <div class="preview-grid">
        <div v-for="r in previewRows" :key="r.lineId" class="preview-item">
          <div class="preview-item__head">
            <b>{{ r.lineName }}</b>
            <span class="t-mono t-subtle">{{ r.ch?.channel }} · {{ r.ch?.stream }}</span>
          </div>
          <div class="preview-item__body">
            <iframe
              :src="go2rtcUrl('mse', r.ch?.stream ?? '')"
              allow="autoplay; fullscreen; encrypted-media"
              referrerpolicy="no-referrer"
              scrolling="no"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- ── 通道列表 ─────────────────────────────────────── -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">通道列表</h3>
        <span class="card__extra">共 {{ rows.length }} 路，其中 {{ rows.filter((r) => r.ch && r.ch.enabled !== false).length }} 路已接入</span>
      </div>

      <el-table :data="rows" size="default">
        <el-table-column prop="lineId" label="产线" width="80" />
        <el-table-column prop="lineName" label="产线名称" width="110" />
        <el-table-column label="通道号" width="100">
          <template #default="{ row }">
            <el-input v-model="row.ch.channel" size="small" placeholder="CH01" />
          </template>
        </el-table-column>
        <el-table-column label="流名称（stream）" width="150">
          <template #default="{ row }">
            <el-input v-model="row.ch.stream" size="small" placeholder="go2rtc 流名" />
          </template>
        </el-table-column>
        <el-table-column label="RTSP 源" min-width="300">
          <template #default="{ row }">
            <el-input v-model="row.ch.rtsp" size="small" placeholder="rtsp://user:pass@ip:554/..." />
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.ch.enabled" size="small" />
          </template>
        </el-table-column>
        <el-table-column label="配置校验" width="110">
          <template #default="{ row }">
            <el-tag :type="streamStatus(row.ch).type" size="small">{{ streamStatus(row.ch).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :disabled="!row.ch.stream || row.ch.enabled === false"
              @click="openSnapshot(row.ch.stream)"
            >
              抓图
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ── 配置指引 ─────────────────────────────────────── -->
    <div class="card">
      <div class="card__head">
        <h3 class="card__title">go2rtc 配置要点</h3>
      </div>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="① 网关配置文件">
          go2rtc 目录下的 <span class="t-mono">go2rtc.yaml</span>，
          <span class="t-mono">streams:</span> 下每个 key 就是一个流名称
        </el-descriptions-item>
        <el-descriptions-item label="② 端口">
          <span class="t-mono">api.listen: ":1984"</span>（HTTP API + 播放页 + MSE/HLS/MJPEG）、
          <span class="t-mono">webrtc.listen: ":8555"</span>（仅 WebRTC 需要）
        </el-descriptions-item>
        <el-descriptions-item label="③ 跨域">
          前端直连网关时需 <span class="t-mono">api.origin: "*"</span>，
          生产环境建议改为前端实际域名
        </el-descriptions-item>
        <el-descriptions-item label="④ 摄像头并发">
          海康/大华默认并发路数常见 4—6 路。go2rtc 按需拉流，
          但同一流被多端同时观看仍会占用并发，建议大屏与后台不要长时间同时预览同一路
        </el-descriptions-item>
        <el-descriptions-item label="⑤ ffmpeg（抓图 / MJPEG 依赖）">
          网关侧需安装 <span class="t-mono">ffmpeg</span> 并加入 <span class="t-mono">PATH</span>，
          否则 <span class="t-mono">/api/frame.jpeg</span>（本页「抓图」）返回 500、
          <span class="t-mono">/api/stream.mjpeg</span> 返回空响应。
          <b>HLS 与 MSE 不依赖 ffmpeg</b>，所以画面本身不受影响
        </el-descriptions-item>
        <el-descriptions-item label="⑤ 新增产线摄像头">
          先在 <span class="t-mono">go2rtc.yaml</span> 的 <span class="t-mono">streams</span> 下加一行
          （如 <span class="t-mono">a2: rtsp://...</span>），重启网关；
          再到本页「通道列表」填该产线的
          <span class="t-mono">RTSP 源</span> 与 <span class="t-mono">流名称（stream）</span>，
          打开 <span class="t-mono">启用</span> 并保存
        </el-descriptions-item>
      </el-descriptions>
    </div>
  </div>
</template>

<style scoped lang="scss">
.mb-16 { margin-bottom: 16px; }
.mb-12 { margin-bottom: 12px; }
.mt-12 { margin-top: 12px; }
.card + .card { margin-top: 16px; }

.gateway-form {
  :deep(.el-form-item) { margin-bottom: 8px; }
}

.preview-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
}

.preview-item {
  border: 1px solid $yl-border;
  border-radius: 4px;
  overflow: hidden;
}

.preview-item__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  background: #f7f9fb;
  border-bottom: 1px solid $yl-border;
  font-size: 12px;

  b { font-weight: 500; color: #0c0d0e; }
}

.preview-item__body {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #0c0d0e;

  iframe {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: 0;
  }
}
</style>
