<script setup lang="ts">
/** NotFound —— 404 页 */
import { useRouter } from 'vue-router';
import BrandMark from '@/layout/BrandMark.vue';

const router = useRouter();
</script>

<template>
  <div class="nf">
    <BrandMark :size="28" />
    <h1>404</h1>
    <p>页面不存在或已被移除</p>
    <div class="nf__actions">
      <el-button @click="router.back()">返回上一页</el-button>
      <el-button type="primary" @click="router.replace('/dashboard')">回到数据中心</el-button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.nf {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: #f7f9fb;

  h1 {
    margin: 8px 0 0;
    font-size: 56px;
    font-weight: 600;
    color: #0c0d0e;
    letter-spacing: 2px;
  }

  p {
    margin: 0;
    font-size: 13px;
    color: $yl-text-subtle;
  }
}

.nf__actions {
  margin-top: 12px;
  display: flex;
  gap: 8px;
}
</style>
