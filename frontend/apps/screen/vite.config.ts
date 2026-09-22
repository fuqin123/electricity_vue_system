import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  base: './', // 相对路径，静态目录下任意层级均可部署（Nginx / IIS / Tomcat / file://）
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
        // 共享设计令牌全局注入，组件内可直接使用 SCSS 变量
        additionalData: `@use "@eep/shared/styles/tokens.screen.scss" as *;`
      }
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    open: false,
    proxy: {
      // 接入真实后端时打开：把 /api 代理到 Django 服务
      // '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts'],
          vue: ['vue', 'vue-router', 'pinia', '@vueuse/core']
        }
      }
    }
  }
});
