import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { applyVideoConfig, energyApi } from '@eep/shared';
import App from './App.vue';
import router from './router';
import './styles/app.scss';

const app = createApp(App);

app.use(createPinia());
app.use(router);

/**
 * 运行时错误可视化：开发 / 交付验收期把报错直接显示在屏幕上，
 * 生产构建（import.meta.env.PROD）自动关闭。
 */
if (!import.meta.env.PROD) {
  const box = document.createElement('div');
  box.className = 'js-err';
  document.body.appendChild(box);
  const show = (msg: string) => {
    box.textContent = `⚠ 前端异常：${msg}`;
    box.classList.add('is-on');
  };
  window.addEventListener('error', (e) =>
    show(`${e.message} @ ${(e.filename || '').split('/').pop()}:${e.lineno}`)
  );
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason as { message?: string } | undefined;
    show(`Promise 未捕获：${r?.message ?? String(e.reason)}`);
  });
}

/**
 * v1.7.5：视频网关 / RTSP 通道由管理后台维护，大屏启动时先拉一次再挂载。
 * SDK 内部已兜底（不可达时退回内置默认值），这里不再阻塞出错。
 */
(async () => {
  // 最多等 3 秒：网关配置接口慢不能拖住整块大屏，超时就按内置默认值启动
  const cfg = await Promise.race([
    energyApi.getVideoConfig(),
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000))
  ]);
  applyVideoConfig(cfg);
  app.mount('#app');
})();
