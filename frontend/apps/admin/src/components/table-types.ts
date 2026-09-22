/**
 * 列表页列定义
 *
 * 为什么单独抽成 .ts 文件：
 *   泛型 SFC 的 `export interface` 会脱离泛型作用域，在接口内部引用泛型参数 T
 *   会被编译器判定为「找不到名称 T」，因此类型必须外置。
 *
 * 关于泛型默认值为什么是 `any`：
 *   TablePage 的泛型 T 由 `fetch` 的返回类型推断（如 AlarmRecord）。
 *   若此处默认值写成 `Record<string, unknown>`，各页面声明的 `TableColumn[]`
 *   将无法赋值给 `TableColumn<AlarmRecord>[]` —— 因为 TS 不会给 interface
 *   生成隐式索引签名，函数参数的逆变检查会直接失败。
 *   故默认值取 `any`，让列定义在「不给泛型」时也能安全传入。
 *
 * 需要严格类型时，在页面侧显式声明即可：`TableColumn<LineMaster>[]`
 */
export interface TableColumn<T = any> {
  /** 字段名 */
  prop: string;
  label: string;
  width?: number | string;
  minWidth?: number | string;
  align?: 'left' | 'center' | 'right';
  /** 数值列使用等宽字体并右对齐 */
  mono?: boolean;
  /** 文本格式化 */
  formatter?: (row: T) => string;
  /** 具名插槽名，优先级高于 formatter */
  slot?: string;
  /** 是否显示溢出省略（默认 true） */
  showOverflow?: boolean;
}
