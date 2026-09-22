import { createApp } from 'vue';
import { createPinia } from 'pinia';
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

app.mount('#app');
