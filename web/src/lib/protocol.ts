// Message shapes sent by radar/engine.py over /ws/live.

export type DetState = "calibrating" | "empty" | "settled" | "motion";
export type SourceKind = "live" | "sim" | "replay" | "none";

export interface StreamInfo {
  name: string;
  kind: string;
  unit: string;
  nominal_hz: number;
  group: string;
  label: string;
  anchor: string | null;
  resample: string;
  stab_tol: number;
}

export type Series = [number[], (number | null)[]];

export interface StreamStats {
  poll_hz: number | null;
  refresh_hz: number | null;
  quant_step: number | null;
  loss: number | null;
  last: number | null;
  age_s: number | null;
}

export interface RadarEvent {
  kind: string;
  wall: number;
  source?: SourceKind;
  z?: number;
  detail?: string;
  result?: ProbeResult;
}

export interface ProbePhase {
  label: string;
  seconds: number;
  prompt?: string;
}

export interface ProbeStatus {
  stage: "lead_in" | "phase" | "done";
  index: number;
  total: number;
  remaining?: number;
  elapsed?: number;
  settling?: boolean;
  phase?: ProbePhase;
  next?: ProbePhase;
}

export interface ProbeStreamResult {
  auc: number | null;
  verdict: "strong" | "usable" | "weak" | "none" | "no data";
  spread_ratio: number | null;
  n_still: number;
  n_moving: number;
  weight: number;
}

export interface ProbeResult {
  wall_start: number;
  phases: ProbePhase[];
  streams: Record<string, ProbeStreamResult>;
  best: ({ stream: string } & ProbeStreamResult) | null;
  detector: { hit_rate: number | null; false_alarm_rate: number | null };
  overall: string;
  source?: SourceKind;
  session?: string | null;
  applied_weights?: boolean;
}

export interface RecStatus {
  id: string;
  elapsed: number;
  label: string | null;
  n: number;
  probe: boolean;
}

export interface ReplayStatus {
  session: string;
  t: number;
  duration: number;
  speed: number;
  paused: boolean;
  label: string | null;
}

export interface Truth {
  state: "empty" | "still" | "walking";
  x: number | null;
  y: number | null;
  load: boolean;
  mode: string;
}

export interface Layout {
  width: number;
  height: number;
  router: [number, number];
  laptop: [number, number];
  nodes: Record<string, [number, number]>;
  entrance: [number, number];
  walls: [number, number, number, number][];
  rooms: { name: string; x: number; y: number; w: number; h: number }[];
}

export interface SourceInfo {
  kind: SourceKind;
  name: string;
  simulated?: boolean;
  layout?: Layout;
  session?: string;
  duration?: number;
  labels?: { start: number; end: number; label: string }[];
  parts?: Record<string, unknown>[];
}

export interface DetectorConfig {
  z_on: number;
  z_off: number;
  on_windows: number;
  hold_s: number;
  presence_hold_s: number;
  cusum_k: number;
  cusum_h: number;
  calib_s: number;
  baseline_len: number;
  z_clip: number;
}

export interface Guard {
  armed: boolean;
  armed_at: number | null;
  webhook: string | null;
}

export interface Hello {
  type: "hello";
  t: number;
  source: SourceInfo | null;
  streams: StreamInfo[];
  detector: DetectorConfig;
  guard: Guard;
  rec: RecStatus | null;
  probe: ProbeStatus | null;
  last_probe: ProbeResult | null;
  protocol: ProbePhase[];
  history: Record<string, Series>;
  det_history: [number, number, number][];
  events: RadarEvent[];
  window_s: number;
  hop_s: number;
}

export interface StreamFrame {
  a: number | null;
  z: number | null;
  w: number;
  q: number;
}

export interface Frame {
  type: "frame";
  seq: number;
  t: number;
  wall: number;
  source: SourceKind;
  det: {
    state: DetState;
    z: number | null;
    cusum: number | null;
    p: number | null;
    last_motion: number | null;
    calib: number;
  };
  streams: Record<string, StreamFrame>;
  traces: Record<string, Series>;
  events: RadarEvent[];
  spec?: { stream: string; fs: number; bins: number[] };
  truth?: Truth;
  replay?: ReplayStatus;
  rec?: RecStatus;
  probe?: ProbeStatus;
  stats?: Record<string, StreamStats>;
}

export interface ReplayMsg {
  type: "replay";
  replay: ReplayStatus;
}

export type Message = Hello | Frame | ReplayMsg;

export const STATE_CODE: Record<number, DetState> = {
  0: "calibrating",
  1: "empty",
  2: "settled",
  3: "motion",
};
