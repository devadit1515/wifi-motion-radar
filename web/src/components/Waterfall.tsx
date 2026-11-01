import { useRef } from "react";
import { palette, setFont, useCanvasLoop } from "../lib/canvas";
import { quantile } from "../lib/ring";
import { store } from "../lib/store";

function hexToRgb(c: string): [number, number, number] {
  const m = c.trim().replace("#", "");
  const n = parseInt(m.length === 3 ? m.replace(/(.)/g, "$1$1") : m, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function mix(a: [number, number, number], b: [number, number, number], k: number): string {
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * k)).join(",")})`;
}

/**
 * Spectrum of the most informative fast stream, one column per frame, newest on the
 * right. Frequency is on a log axis from 0.3 Hz up to Nyquist. Ink density is power.
 */
export function Waterfall() {
  const range = useRef({ lo: -40, hi: 0, at: 0 }).current;

  const ref = useCanvasLoop((ctx, w, h) => {
    const p = palette();
    const cols = store.spec.cols;
    const L = 46;
    const B = 20;
    const plotW = w - L - 8;
    const plotH = h - B - 6;
    const now = performance.now();
    if (cols.length && now - range.at > 500) {
      const flat = cols.slice(-120).flat().sort((a, b) => a - b);
      range.lo = quantile(flat, 0.05);
      range.hi = quantile(flat, 0.995);
      range.at = now;
    }
    const paper = hexToRgb(p.raised);
    const ink = hexToRgb(p.ink);
    const n = 360;
    const cw = plotW / n;
    cols.forEach((col, i) => {
      const x = L + plotW - (cols.length - i) * cw;
      const bh = plotH / col.length;
      col.forEach((db, j) => {
        const k = Math.min(1, Math.max(0, (db - range.lo) / (range.hi - range.lo || 1)));
        ctx.fillStyle = mix(paper, ink, Math.pow(k, 1.4));
        ctx.fillRect(x, 6 + plotH - (j + 1) * bh, cw + 0.6, bh + 0.6);
      });
    });

    const fs = store.last?.spec?.fs ?? 100;
    const fMax = fs / 2;
    const yOf = (f: number) => 6 + plotH - (Math.log(f / 0.3) / Math.log(fMax / 0.3)) * plotH;
    setFont(ctx, 11, 460, "condensed");
    ctx.fillStyle = p.inkSoft;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ctx.strokeStyle = p.rule;
    for (const f of [0.3, 1, 3, 10, 30]) {
      if (f > fMax) continue;
      const y = yOf(f);
      ctx.fillText(`${f} Hz`, L - 6, y);
      ctx.beginPath();
      ctx.moveTo(L, Math.round(y) + 0.5);
      ctx.lineTo(L + plotW, Math.round(y) + 0.5);
      ctx.stroke();
    }
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillText(`−${Math.round(n / 10)} s`, L, h);
    ctx.textAlign = "right";
    ctx.fillText("now", L + plotW, h);
    if (!cols.length) {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Waiting for a fast stream", L + plotW / 2, plotH / 2);
    }
  });

  return <canvas ref={ref} className="waterfall" role="img" aria-label="Spectrogram of the primary stream" />;
}
