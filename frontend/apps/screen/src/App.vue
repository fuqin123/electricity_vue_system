<script setup lang="ts">
/**
 * App —— 大屏外壳
 *  · 1920×1080 舞台承载两块大屏（由路由切换视图内容）
 *  · 全局快捷键：1 总览 / 2 单线详情 / F 全屏 / R 立即刷新 / V 切换视频链路（单线页）
 *  · 首屏数据未就绪时显示引导态，避免白屏
 */
import { onBeforeUnmount, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import StageFrame from '@/components/StageFrame.vue';
import { useEnergyStore } from '@/stores/energy';
import { useToast } from '@/composables/useClock';

const store = useEnergyStore();
const router = useRouter();
const route = useRoute();
const { toastText, toastOn, toast } = useToast();

function onKeydown(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  if (t && /INPUT|TEXTAREA|SELECT/.test(t.tagName)) return;

  if (e.key === '1') void router.push({ name: 'overview' });
  if (e.key === '2') {
    const lineId = (route.params.lineId as string) || store.currentLineId;
    void router.push({ name: 'line', params: { lineId } });
  }
  if (e.key === 'r' || e.key === 'R') {
    void store.refresh().then(() => toast('已刷新'));
  }
}

onMounted(() => {
  store.start();
  document.addEventListener('keydown', onKeydown);
});

onBeforeUnmount(() => {
  store.stop();
  document.removeEventListener('keydown', onKeydown);
});
</script>

<template>
  <StageFrame>
    <RouterView />
  </StageFrame>

  <!--
    降级提示：后端接口不可达时会退回演示数据。
    这条必须显示 —— 演示数据与真实采集数据在界面上长得一模一样，
    不提示的话现场会拿它当真实产量/用电做判断（比白屏危险得多）。
  -->
  <div v-if="store.notice" class="degrade">
    <b>演示数据</b>
    <span>{{ store.notice }}</span>
  </div>

  <!-- 首屏加载态：数据未就绪时不渲染空面板 -->
  <div v-if="!store.ready" class="boot">
    <span>ENERGY &amp; OUTPUT DATA CENTER</span>
    <div class="boot__bar"><i /></div>
    <span style="font-size: 11px; color: var(--t5)">
      {{ store.errorMsg ? `数据获取失败：${store.errorMsg}` : '正在获取车间实时数据…' }}
    </span>
  </div>

  <div class="toast" :class="{ 'is-on': toastOn }">{{ toastText }}</div>
</template>
