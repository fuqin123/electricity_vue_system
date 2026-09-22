/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>;
  export default component;
}

/** flv.js 无官方 TS 类型声明，按使用面窄声明即可 */
declare module 'flv.js' {
  export interface FlvPlayer {
    attachMediaElement(el: HTMLMediaElement): void;
    load(): void;
    play(): Promise<void>;
    destroy(): void;
    on(event: string, cb: (...args: unknown[]) => void): void;
  }
  interface FlvJs {
    createPlayer(mediaDataSource: Record<string, unknown>, config?: Record<string, unknown>): FlvPlayer;
    isSupported(): boolean;
    Events: Record<string, string>;
  }
  const flvjs: FlvJs;
  export default flvjs;
}
