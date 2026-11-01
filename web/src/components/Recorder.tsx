import { useRef } from "react";
import { palette, setFont, useCanvasLoop, type Palette } from "../lib/canvas";
import { quantile, type Ring } from "../lib/ring";
import { store } from "../lib/store";

export interface Lane {
  stream: string;
  label: string;
  unit: string;
  tol: number;
}

interface Props {
  lanes: Lane[];
  windowS: number;
  zOn: number;
  laneHeight?: number;
  title: string;
}

const MOTION_H = 76;
const AXIS_H = 22;

/**
 * The chart recorder. One pen for the fused motion score on top, then one lane per
 * stream drawn in ink. Paper scrolls right to left; red marks motion and lost probes.
 */
export function Recorder({ lanes, windowS, zOn, laneHeight = 46, title }: Props) {
  const scales = useRef(new Map<string, { lo: number; hi: number; at: number }>());
  const height = MOTION_H + lanes.length * laneHeight + AXIS_H;

  const ref = useCanvasLoop((ctx, w, h) => {
    const p = palette();
    const narrow = w < 640;
    const L = narrow ? 8 : 156;
    const R = narrow ? 8 : 70;
    const plotW = Math.max(10, w - L - R);
    const now = store.nowT();
    const t0 = now - windowS;
    const x = (t: number) => L + ((t - t0) / windowS) * plotW;

    drawTimeGrid(ctx, p, t0, now, x, h - AXIS_H, L, plotW);
    drawMotionLane(ctx, p, t0, x, L, plotW, zOn, narrow);

    lanes.forEach((lane, i) => {
      const y0 = MOTION_H + i * laneHeight;
      const ring = store.traces.get(lane.stream);
      ctx.strokeStyle = p.rule;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, y0 + 0.5);
      ctx.lineTo(w, y0 + 0.5);
      ctx.stroke();
      if (!ring) return;

      // Mark a stream that is above threshold *now* in the gutter only, so the red
      // never paints over history from before it was disturbed.
      const z = store.streamState[lane.stream]?.z ?? null;
      const hot = z !== null && z > zOn;
      if (hot) {
        ctx.fillStyle = p.redWash;
        ctx.fillRect(0, y0 + 1, narrow ? 4 : L - 6, laneHeight - 1);
        ctx.fillStyle = p.red;
        ctx.fillRect(0, y0 + 1, 3, laneHeight - 1);
      }
      const sc = scaleFor(scales.current, lane, ring, t0);
      const top = y0 + 7;
      const bot = y0 + laneHeight - 7;
      const y = (v: number) => bot - ((v - sc.lo) / (sc.hi - sc.lo)) * (bot - top);
      drawTrace(ctx, ring, t0, x, y, p.ink, p.red, bot + 5, L, L + plotW);

      setFont(ctx, narrow ? 11 : 12, 560);
      ctx.textBaseline = "top";
      ctx.textAlign = "left";
      if (narrow) {
        // On phones the label sits over the plot, so give it a paper backing.
        ctx.fillStyle = p.raised;
        ctx.fillRect(L, y0 + 4, ctx.measureText(lane.label).width + 6, 16);
      }
      ctx.fillStyle = hot ? p.red : p.ink;
      ctx.fillText(lane.label, narrow ? L + 2 : 12, y0 + 7);
      if (!narrow) {
        setFont(ctx, 11, 460);
        ctx.fillStyle = p.inkSoft;
        const zs = z === null ? "" : `  z ${z.toFixed(1)}`;
        ctx.fillText(`${lane.unit}${zs}`, 12, y0 + 23);
        const last = ring.last();
        setFont(ctx, 13, 600, "condensed");
        ctx.fillStyle = hot ? p.red : p.ink;
        ctx.textAlign = "right";
        ctx.fillText(last === null ? "–" : fmtValue(last, lane.unit), w - 10, y0 + 15);
      }
    });

    // Time axis.
    setFont(ctx, 11, 460);
    ctx.fillStyle = p.inkSoft;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    const step = windowS > 40 ? 10 : 5;
    for (let s = 0; s <= windowS; s += step) {
      const tx = L + plotW - (s / windowS) * plotW;
      ctx.fillText(s === 0 ? "now" : `−${s} s`, Math.min(Math.max(tx, L + 14), L + plotW - 12), h - AXIS_H / 2);
    }
  });

  return (
    <figure className="recorder">
      <figcaption className="sr-only">{title}</figcaption>
      <canvas ref={ref} style={{ width: "100%", height }} role="img" aria-label={title} />
    </figure>
  );
}

function fmtValue(v: number, unit: string): string {
  const a = Math.abs(v);
  const d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return `${v.toFixed(d)} ${unit === "ratio" ? "" : unit}`.trim();
}

function scaleFor(cache: Map<string, { lo: number; hi: number; at: number }>, lane: Lane, ring: Ring, t0: number) {
  const now = performance.now();
  const c = cache.get(lane.stream);
  if (c && now - c.at < 300) return c;
  const vals = ring.valuesSince(t0).sort((a, b) => a - b);
  let lo = quantile(vals, 0.02);
  let hi = quantile(vals, 0.98);
  if (!Number.isFinite(lo)) {
    lo = 0;
    hi = 1;
  }
  const minSpan = Math.max(lane.tol * 2, 1e-6);
  if (hi - lo < minSpan) {
    const mid = (hi + lo) / 2;
    lo = mid - minSpan / 2;
    hi = mid + minSpan / 2;
  }
  const pad = (hi - lo) * 0.15;
  const next = c
    ? { lo: c.lo + (lo - pad - c.lo) * 0.35, hi: c.hi + (hi + pad - c.hi) * 0.35, at: now }
    : { lo: lo - pad, hi: hi + pad, at: now };
  cache.set(lane.stream, next);
  return next;
}

function drawTimeGrid(
  ctx: CanvasRenderingContext2D, p: Palette, t0: number, now: number,
  x: (t: number) => number, bottom: number, L: number, plotW: number,
) {
  ctx.lineWidth = 1;
  for (let s = Math.ceil(t0); s <= now; s++) {
    const xx = Math.round(x(s)) + 0.5;
    if (xx < L || xx > L + plotW) continue;
    ctx.strokeStyle = s % 5 === 0 ? p.gridMajor : p.grid;
    ctx.beginPath();
    ctx.moveTo(xx, 0);
    ctx.lineTo(xx, bottom);
    ctx.stroke();
  }
}

function drawMotionLane(
  ctx: CanvasRenderingContext2D, p: Palette, t0: number, x: (t: number) => number,
  L: number, plotW: number, zOn: number, narrow: boolean,
) {
  const top = 10;
  const bot = MOTION_H - 10;
  const { detT, detZ, detS } = store;
  const start = Math.max(0, detT.lowerBound(t0) - 1);

  // Shade motion (red) and baseline learning (grid) behind the pen.
  for (let k = start; k < detT.size; k++) {
    const s = detS.v[detS.at(k)];
    if (s !== 3 && s !== 0) continue;
    const ta = detT.v[detT.at(k)];
    const tb = k + 1 < detT.size ? detT.v[detT.at(k + 1)] : ta + 0.1;
    const xa = Math.max(L, x(ta));
    const xb = Math.min(L + plotW, x(tb) + 0.5);
    if (xb <= xa) continue;
    ctx.fillStyle = s === 3 ? p.redWash : p.grid;
    ctx.fillRect(xa, 2, xb - xa, MOTION_H - 4);
    if (s === 3) {
      ctx.fillStyle = p.red;
      ctx.fillRect(xa, 2, xb - xa, 3);
    }
  }

  let zMax = zOn * 2;
  for (let k = start; k < detZ.size; k++) zMax = Math.max(zMax, detZ.v[detZ.at(k)]);
  zMax = Math.min(zMax, 16);
  const y = (z: number) => bot - (Math.max(Math.min(z, zMax), -1) / zMax) * (bot - top);

  ctx.setLineDash([4, 4]);
  ctx.strokeStyle = p.red;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(L, Math.round(y(zOn)) + 0.5);
  ctx.lineTo(L + plotW, Math.round(y(zOn)) + 0.5);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.save();
  ctx.beginPath();
  ctx.rect(L, 0, plotW, MOTION_H);
  ctx.clip();
  ctx.strokeStyle = p.blue;
  ctx.lineWidth = 1.75;
  ctx.lineJoin = "round";
  ctx.beginPath();
  let started = false;
  for (let k = start; k < detZ.size; k++) {
    const xx = x(detT.v[detT.at(k)]);
    const yy = y(detZ.v[detZ.at(k)]);
    if (!started) {
      ctx.moveTo(xx, yy);
      started = true;
    } else ctx.lineTo(xx, yy);
  }
  ctx.stroke();
  ctx.restore();

  setFont(ctx, narrow ? 11 : 12, 640);
  ctx.fillStyle = p.ink;
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.fillText("Motion score", narrow ? L + 2 : 12, 10);
  if (!narrow) {
    setFont(ctx, 11, 460);
    ctx.fillStyle = p.inkSoft;
    ctx.fillText(`threshold ${zOn}`, 12, 26);
  }
}

function drawTrace(
  ctx: CanvasRenderingContext2D, ring: Ring, t0: number, x: (t: number) => number,
  y: (v: number) => number, ink: string, red: string, lossY: number, xMin: number, xMax: number,
) {
  const start = Math.max(0, ring.lowerBound(t0) - 1);
  ctx.save();
  ctx.beginPath();
  ctx.rect(xMin, 0, xMax - xMin, 1e5);
  ctx.clip();
  ctx.strokeStyle = ink;
  ctx.lineWidth = 1.25;
  ctx.lineJoin = "round";
  ctx.beginPath();
  let col = -1;
  let cMin = 0;
  let cMax = 0;
  let cLast = 0;
  let open = false;
  const losses: number[] = [];
  const flush = () => {
    if (col < 0) return;
    if (!open) {
      ctx.moveTo(col, cMin);
      open = true;
    }
    ctx.lineTo(col, cMin);
    ctx.lineTo(col, cMax);
    ctx.lineTo(col, cLast);
  };
  for (let k = start; k < ring.size; k++) {
    const i = ring.at(k);
    const xx = x(ring.t[i]);
    if (xx < xMin - 2 || xx > xMax + 2) continue;
    const v = ring.v[i];
    if (Number.isNaN(v)) {
      losses.push(xx);
      continue;
    }
    const c = Math.round(xx);
    const yy = y(v);
    if (c !== col) {
      flush();
      col = c;
      cMin = cMax = cLast = yy;
    } else {
      cMin = Math.min(cMin, yy);
      cMax = Math.max(cMax, yy);
      cLast = yy;
    }
  }
  flush();
  ctx.stroke();
  ctx.restore();

  if (losses.length) {
    ctx.strokeStyle = red;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (const lx of losses) {
      ctx.moveTo(Math.round(lx) + 0.5, lossY - 5);
      ctx.lineTo(Math.round(lx) + 0.5, lossY);
    }
    ctx.stroke();
  }
}
