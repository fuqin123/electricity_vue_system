/**
 * @eep/shared —— 能源产效管理系统共享层
 *
 * 包含三部分：
 *  1. 领域类型（types.ts）        —— 大屏 / 后台统一的数据契约
 *  2. 全局配置（config.ts）       —— 产线定义、能耗模型、阈值、视频通道、配色
 *  3. API SDK（api/index.ts）     —— mock ↔ 真实接口的唯一切换点
 *  4. 格式化工具（format.ts）     —— 数值 / 时间 / 确定性随机
 *
 * 设计令牌以 SCSS 形式单独提供，通过 exports 字段按需引入：
 *   @use '@eep/shared/styles/tokens.screen.scss';
 *   @use '@eep/shared/styles/tokens.admin.scss';
 */

export * from './types';
export * from './config';
export * from './format';
export * from './api';
export { buildSnapshot, hourSample, daySample, PROFILE, PLAN_RATE } from './mock/energy';
export { mockAdminOverview, paginate } from './mock/admin';
