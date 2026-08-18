/**
 * 多路视频同步控制器。
 *
 * 多视角回放的核心难点：各相机流的起始偏移不同（`SensorStream.start_offset_ms`），
 * 且 `<video>` 元素的 seek 是异步的。做法是维护一个**逻辑主时钟**，
 * 各路 video 只是它的从动者 —— 不用某一路 video 的 currentTime 当基准，
 * 否则该路卡顿会把其余路一起带偏。
 */

/** 允许的最大漂移。超过则强制 seek 纠偏。 */
const MAX_DRIFT_MS = 80;

/** 一路视频轨。 */
export interface Track {
  /** 对应的 topic，用于与 SensorStream 关联 */
  readonly topic: string;
  /** 相对 Episode 起点的偏移 */
  readonly startOffsetMs: number;
  readonly element: HTMLVideoElement;
}

export interface SyncState {
  readonly positionMs: number;
  readonly playing: boolean;
  readonly rate: number;
}

/**
 * 同步控制器。
 *
 * 用法：`attach()` 注册各路视频，`seek()` / `play()` / `pause()` 驱动主时钟，
 * 控制器负责把各路 video 拉到正确位置。
 */
export class SyncController {
  private tracks: Track[] = [];
  private positionMs = 0;
  private playing = false;
  private rate = 1;
  private rafHandle: number | null = null;
  private lastTickMs: number | null = null;
  private listeners = new Set<(state: SyncState) => void>();

  /** 注册一路视频。 */
  attach(track: Track): void {
    this.tracks = [...this.tracks, track];
    this.applyToTrack(track);
  }

  /** 注销一路视频。 */
  detach(topic: string): void {
    this.tracks = this.tracks.filter((t) => t.topic !== topic);
  }

  /** 订阅状态变化，返回取消订阅函数。 */
  subscribe(listener: (state: SyncState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  get state(): SyncState {
    return { positionMs: this.positionMs, playing: this.playing, rate: this.rate };
  }

  /** 跳到指定位置。所有轨道一起 seek。 */
  seek(positionMs: number): void {
    this.positionMs = Math.max(0, positionMs);
    this.tracks.forEach((track) => this.applyToTrack(track));
    this.emit();
  }

  /** 播放。 */
  play(): void {
    if (this.playing) return;
    this.playing = true;
    this.lastTickMs = null;
    this.tracks.forEach((track) => void track.element.play().catch(() => undefined));
    this.startTicking();
    this.emit();
  }

  /** 暂停。 */
  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    this.tracks.forEach((track) => track.element.pause());
    this.stopTicking();
    this.emit();
  }

  /** 设置倍速。 */
  setRate(rate: number): void {
    this.rate = rate;
    this.tracks.forEach((track) => {
      track.element.playbackRate = rate;
    });
    this.emit();
  }

  /** 释放资源。组件卸载时必须调用，否则 rAF 循环泄漏。 */
  dispose(): void {
    this.stopTicking();
    this.listeners.clear();
    this.tracks = [];
  }

  /** 把主时钟位置映射到单个轨道并纠偏。 */
  private applyToTrack(track: Track): void {
    const targetSeconds = Math.max(0, (this.positionMs - track.startOffsetMs) / 1000);
    const driftMs = Math.abs(track.element.currentTime * 1000 - targetSeconds * 1000);
    // 只在漂移超阈值时 seek —— 每帧都 seek 会让播放卡顿
    if (driftMs > MAX_DRIFT_MS) {
      track.element.currentTime = targetSeconds;
    }
  }

  private startTicking(): void {
    const tick = (timestamp: number): void => {
      if (!this.playing) return;
      if (this.lastTickMs !== null) {
        this.positionMs += (timestamp - this.lastTickMs) * this.rate;
        this.tracks.forEach((track) => this.applyToTrack(track));
        this.emit();
      }
      this.lastTickMs = timestamp;
      this.rafHandle = requestAnimationFrame(tick);
    };
    this.rafHandle = requestAnimationFrame(tick);
  }

  private stopTicking(): void {
    if (this.rafHandle !== null) {
      cancelAnimationFrame(this.rafHandle);
      this.rafHandle = null;
    }
    this.lastTickMs = null;
  }

  private emit(): void {
    const snapshot = this.state;
    this.listeners.forEach((listener) => listener(snapshot));
  }
}
