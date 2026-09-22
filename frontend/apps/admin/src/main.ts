import { createApp } from 'vue';
import { createPinia } from 'pinia';
import ElementPlus from 'element-plus';
import zhCn from 'element-plus/es/locale/lang/zh-cn';
import 'element-plus/dist/index.css';
import * as ElementPlusIconsVue from '@element-plus/icons-vue';

import App from './App.vue';
import router from './router';
import './styles/index.scss';

const app = createApp(App);

app.use(createPinia());
app.use(router);
// 全局注册 Element Plus 图标组件，模板中可直接用 <el-icon><Refresh /></el-icon>
app.use(ElementPlus, { locale: zhCn, size: 'default', zIndex: 3000 });

Object.entries(ElementPlusIconsVue).forEach(([key, component]) => {
  app.component(key, component);
});

app.mount('#app');
