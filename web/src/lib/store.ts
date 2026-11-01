import { useSyncExternalStore } from "react";
import { Ring } from "./ring";
import {
  STATE_CODE,
  type DetState,
  type Frame,
  type Hello,
  type Message,
  type ProbeResult,
  type RadarEvent,
  type ReplayStatus,
  type StreamFrame,
  type StreamInfo,
  type StreamStats,
} from "./protocol";

const HISTORY_S = 90;
const SPEC_COLUMNS = 360;
const UI_INTERVAL_MS = 125; // React re-renders at most 8 times a second

export type Conn = "connecting" | "open" | "closed";

/**
 * One WebSocket, one source of truth. Samples land in typed ring buffers that the
 * canvases read every animation frame; React components subscribe to a version
 * counter that ticks at a throttled rate for the low-frequency parts of the UI.
 */
class LiveStore {
  conn: Conn = "connecting";
  hello: Hello | null = null;
  streams: StreamInfo[] = [];
  traces = new Map<string, Ring>();
  detT = new Ring(Math.ceil(HISTORY_S * 10 * 1.2));
  detZ = new Ring(this.detT.capacity);
  detS = new Ring(this.detT.capacity);
  spec: { cols: number[][]; stream: string | null } = { cols: [], stream: null };
  last: Frame | null = null;
  lastArrival = 0;
  streamState: Record<string, StreamFrame> = {};
  stats: Record<string, StreamStats> = {};
  events: RadarEvent[] = [];
  lastProbe: ProbeResult | null = null;
  replay: ReplayStatus | null = null;
  version = 0;

  private listeners = new Set<() => void>();
  private pending = false;
  private retry = 500;
  private displayT = 0;

  connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/live`);
    this.conn = "connecting";
    ws.onopen = () => {
      this.conn = "open";
      this.retry = 500;
      this.bump(true);
    };
    ws.onmessage = (ev) => this.onMessage(JSON.parse(ev.data as string) as Message);
    ws.onclose = () => {
      this.conn = "closed";
      this.bump(true);
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 5000);
    };
  }

  private onMessage(msg: Message): void {
    if (msg.type === "hello") this.onHello(msg);
    else if (msg.type === "frame") this.onFrame(msg);
    else if (msg.type === "replay") {
      this.replay = msg.replay;
      this.bump();
    }
  }

  private onHello(h: Hello): void {
    this.hello = h;
    this.streams = h.streams;
    this.traces.clear();
    for (const s of h.streams) {
      this.traces.set(s.name, new Ring(Math.ceil(Math.max(s.nominal_hz, 5) * HISTORY_S * 1.2)));
    }
    for (const [name, [ts, vs]] of Object.entries(h.history)) {
      const r = this.traces.get(name);
      if (r) for (let i = 0; i < ts.length; i++) r.push(ts[i], vs[i]);
    }
    for (const r of [this.detT, this.detZ, this.detS]) r.clear();
    for (const [t, z, s] of h.det_history) this.pushDet(t, z, s);
    this.spec = { cols: [], stream: null };
    this.events = [...h.events].reverse();
    this.lastProbe = h.last_probe;
    this.last = null;
    this.replay = null;
    this.displayT = h.t;
    this.bump(true);
  }

  private pushDet(t: number, z: number, s: number): void {
    this.detT.push(t, t);
    this.detZ.push(t, z);
    this.detS.push(t, s);
  }

  private onFrame(f: Frame): void {
    for (const [name, [ts, vs]] of Object.entries(f.traces)) {
      const r = this.traces.get(name);
      if (r) for (let i = 0; i < ts.length; i++) r.push(ts[i], vs[i]);
    }
    const code = Object.entries(STATE_CODE).find(([, s]) => s === f.det.state)?.[0] ?? "1";
    this.pushDet(f.t, f.det.z ?? 0, Number(code));
    if (f.spec) {
      if (this.spec.stream !== f.spec.stream) this.spec = { cols: [], stream: f.spec.stream };
      this.spec.cols.push(f.spec.bins);
      if (this.spec.cols.length > SPEC_COLUMNS) this.spec.cols.shift();
    }
    if (f.stats) this.stats = f.stats;
    this.streamState = f.streams;
    this.replay = f.replay ?? null;
    for (const ev of f.events) this.onEvent(ev);
    this.last = f;
    this.lastArrival = performance.now();
    this.bump();
  }

  private onEvent(ev: RadarEvent): void {
    this.events.unshift(ev);
    if (this.events.length > 200) this.events.pop();
    if (ev.kind === "probe_done" && ev.result) this.lastProbe = ev.result;
    if (ev.kind === "guard_alert" && "Notification" in window && Notification.permission === "granted") {
      new Notification("Motion detected", {
        body: `Away Guard saw motion at ${new Date(ev.wall * 1000).toLocaleTimeString()}.`,
        tag: "guard",
      });
    }
  }

  /** Engine time to draw "now" at: extrapolated between frames so the paper scrolls
   *  smoothly, never moving backwards except after a real jump (a seek). */
  nowT(): number {
    const f = this.last;
    if (!f) return this.displayT;
    const speed = this.replay ? (this.replay.paused ? 0 : this.replay.speed) : 1;
    const dt = Math.min((performance.now() - this.lastArrival) / 1000, 0.25) * speed;
    const est = f.t + dt;
    if (est > this.displayT || this.displayT - est > 1) this.displayT = est;
    return this.displayT;
  }

  state(): DetState {
    return this.last?.det.state ?? "calibrating";
  }

  info(name: string): StreamInfo | undefined {
    return this.streams.find((s) => s.name === name);
  }

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getVersion = (): number => this.version;

  private bump(now = false): void {
    if (now) {
      this.version++;
      this.listeners.forEach((fn) => fn());
      return;
    }
    if (this.pending) return;
    this.pending = true;
    setTimeout(() => {
      this.pending = false;
      this.version++;
      this.listeners.forEach((fn) => fn());
    }, UI_INTERVAL_MS);
  }
}

export const store = new LiveStore();

/** Re-render the calling component at the store's throttled UI rate. */
export function useLive(): LiveStore {
  useSyncExternalStore(store.subscribe, store.getVersion);
  return store;
}
