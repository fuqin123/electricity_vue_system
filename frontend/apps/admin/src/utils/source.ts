import { computed, reactive } from 'vue';
import { dataConfig } from '@eep/shared';

/**
 * 页面级「数据源」提示
 *
 * 为什么每页都要说清楚数据来自哪里：
 *   后端接口是分域逐步实现的，同一个系统里会同时存在「真实采集数据」与
 *   「内置演示数据」两种来源。如果不标注，页面看起来一模一样 ——
 *   现场很可能拿演示数据当真实数据做判断，这比报错严重得多。
 *
 * 用法：
 *   const banner = useSourceBanner('admin.meters');
 *   <el-alert v-if="banner.show" type="warning" :title="banner.text" />
 *
 * ⚠ 这里必须用 `reactive()` 包一层，不能直接返回 `{ isRemote, show, text }`：
 *   Vue 的模板**只对顶层的 ref 自动解包**，不会解包普通对象里的嵌套 ref。
 *   直接返回普通对象时，模板里的 `banner.show` 拿到的是 computed 对象本身 ——
 *   它恒为真，于是「演示数据」横幅会在**每一个**页面无条件显示，
 *   包括那些已经接了真实后端的页面（本模块第一版就踩了这个）。
 *   reactive 会把顶层 ref 解包，`banner.show` 才是真正的布尔值。
 */
export function useSourceBanner(key?: string) {
  const isRemote = computed(() => dataConfig.mode === 'api' && (!key || !dataConfig.mockOnly.includes(key)));
  // 总开关就是 mock，或该域还没接后端 → 都是演示数据
  const show = computed(() => !isRemote.value);
  const text = computed(
    () =>
      `本页显示的是**内置演示数据**（${key ?? '当前数据源'} 尚未接入后端接口），不可用于生产判断。` +
      '接入后从 config.ts 的 dataConfig.mockOnly 里移除该域即可。'
  );
  return reactive({ isRemote, show, text });
}
