import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  // history 模式：默认部署在站点根目录。若部署在子路径（如 /admin/），
  // 把 base 改为 '/admin/'，并同步调整 Nginx 的 try_files 配置。
  base: '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@eep/shared': fileURLToPath(new URL('../../packages/shared/src', import.meta.url))
    }
  },
  css: {
    preprocessorOptions: {
      scss: {
        api: 'modern-compiler',
        additionalData: `@use "@eep/shared/styles/tokens.admin.scss" as *;`
      }
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5174,
    open: false,
    proxy: {
      // 接入真实后端：把 /api 代理到 Django 服务
      // （与线上 Nginx 的 location /api/ 反代同构）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  preview: {
    host: '127.0.0.1',
    port: 4174,
    // 生产构建的本地预览也要能连后端，否则看到的永远是降级后的演示数据
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          element: ['element-plus', '@element-plus/icons-vue'],
          vue: ['vue', 'vue-router', 'pinia', '@vueuse/core']
        }
      }
    }
  }
});
