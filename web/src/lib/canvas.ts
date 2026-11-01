import { useEffect, useRef } from "react";
import { cssVar } from "./format";

export type Draw = (ctx: CanvasRenderingContext2D, w: number, h: number, now: number) => void;

/** A canvas sized to its CSS box at device pixel ratio, redrawn every animation frame. */
export function useCanvasLoop(draw: Draw) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let w = 0;
    let h = 0;
    let raf = 0;
    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      w = r.width;
      h = r.height;
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    });
    ro.observe(canvas);
    const loop = (ts: number) => {
      if (w > 0 && h > 0 && !document.hidden) {
        ctx.clearRect(0, 0, w, h);
        drawRef.current(ctx, w, h, ts);
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return ref;
}

export interface Palette {
  paper: string;
  raised: string;
  grid: string;
  gridMajor: string;
  ink: string;
  inkSoft: string;
  inkFaint: string;
  red: string;
  redWash: string;
  blue: string;
  rule: string;
}

let cached: { at: number; p: Palette } | null = null;

/** Theme colours for canvas drawing, re-read at most once a second. */
export function palette(): Palette {
  const now = performance.now();
  if (cached && now - cached.at < 1000) return cached.p;
  const p: Palette = {
    paper: cssVar("--paper"),
    raised: cssVar("--paper-raised"),
    grid: cssVar("--grid"),
    gridMajor: cssVar("--grid-major"),
    ink: cssVar("--ink"),
    inkSoft: cssVar("--ink-soft"),
    inkFaint: cssVar("--ink-faint"),
    red: cssVar("--pen-red"),
    redWash: cssVar("--pen-red-wash"),
    blue: cssVar("--pen-blue"),
    rule: cssVar("--rule"),
  };
  cached = { at: now, p };
  return p;
}

export const FONT = "'Mona Sans Variable', 'Segoe UI', system-ui, sans-serif";

export function setFont(ctx: CanvasRenderingContext2D, size: number, weight = 520, stretch = "semi-condensed") {
  ctx.font = `${weight} ${size}px ${FONT}`;
  // fontStretch is not in every lib.dom version yet.
  (ctx as unknown as { fontStretch: string }).fontStretch = stretch;
}

export const prefersReducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
