/** 大屏组件内部使用的展示型类型 */

/** 自动降级后的大数展示值，如 { v: '8.36', u: '万瓶' } */
export interface BigValue {
  v: string;
  u: string;
}

/** KPI 卡数据 */
export interface KpiCard {
  /** 右上角标签，如「年」「月」「日」「时」 */
  tag: string;
  /** 标签主色 */
  color: string;
  label: string;
  value: BigValue;
  /** 涨跌百分比，null / undefined 表示不显示 */
  delta?: number | null;
  /** true 时下跌视为「好」（如单位电耗） */
  deltaLowerIsBetter?: boolean;
  /** 底部副标题 */
  sub?: string;
}
