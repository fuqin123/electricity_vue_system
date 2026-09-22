import { ElMessage } from 'element-plus';

/**
 * 框架阶段占位提示
 *
 * 本期范围明确为「仅框架：路由 / 菜单 / 页面骨架 / 跳转可通；所有业务函数留空」，
 * 因此所有写操作（新增 / 修改 / 删除 / 导出 / 下发）统一走这里给出明确反馈，
 * 避免出现「点了没反应」的假死感。
 *
 * 接入真实接口时，把调用点替换为实际的 API 调用即可。
 */
export function notImplemented(action: string, todo = '按 docs/01-开发记录及Bug总结.md 的功能清单逐项接入'): void {
  ElMessage({
    type: 'info',
    duration: 2600,
    showClose: true,
    message: `「${action}」：本期为框架阶段，业务函数待接入。${todo}`
  });
}
