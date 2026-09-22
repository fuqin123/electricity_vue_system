<script setup lang="ts">
/** 后台应用根组件：仅承载路由出口与全局过渡 */
</script>

<template>
  <RouterView v-slot="{ Component }">
    <Transition name="fade" mode="out-in">
      <component :is="Component" />
    </Transition>
  </RouterView>
</template>
