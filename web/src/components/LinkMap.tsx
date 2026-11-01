import { useRef } from "react";
import { palette, prefersReducedMotion, setFont, useCanvasLoop } from "../lib/canvas";
import { store } from "../lib/store";

/** Paper-coloured box behind a label so spokes never run through the text. */
function backdrop(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, h: number, fill: string) {
  const w = ctx.measureText(text).width;
  const left = ctx.textAlign === "center" ? x - w / 2 : ctx.textAlign === "right" ? x - w : x;
  ctx.fillStyle = fill;
  ctx.fillRect(left - 3, y - 1, w + 6, h);
}

/**
 * Probe paths, not positions. Each spoke is the round trip from this laptop to one
 * anchor; it trembles in proportion to that path's disturbance (its z-score). Angles
 * are arbitrary: the system does not know where the anchors are.
 */
export function LinkMap({ zOn }: { zOn: number }) {
  const phase = useRef({ v: 0, last: 0 }).current;
  const reduce = prefersReducedMotion();

  const ref = useCanvasLoop((ctx, w, h, ts) => {
    const p = palette();
    const dt = phase.last ? Math.min(0.1, (ts - phase.last) / 1000) : 0;
    phase.last = ts;
    phase.v += dt * 9;
    const rtt = store.streams.filter((s) => s.kind === "rtt");
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) / 2 - 46;

    rtt.forEach((s, i) => {
      const ang = -Math.PI / 2 + (i / Math.max(1, rtt.length)) * Math.PI * 2;
      const nx = cx + Math.cos(ang) * radius;
      const ny = cy + Math.sin(ang) * radius;
      const z = store.streamState[s.name]?.z ?? 0;
      const hot = z > zOn;
      const amp = Math.min(Math.max(z, 0), 12) * 1.1;
      const px = -Math.sin(ang);
      const py = Math.cos(ang);
      const n = 48;
      ctx.strokeStyle = hot ? p.red : p.ink;
      ctx.lineWidth = hot ? 2 : 1.25;
      ctx.beginPath();
      for (let k = 0; k <= n; k++) {
        const f = k / n;
        const env = Math.sin(Math.PI * f);
        const off = reduce ? 0 : amp * env * Math.sin(f * Math.PI * 7 - phase.v + i);
        const lx = cx + (nx - cx) * f + px * off;
        const ly = cy + (ny - cy) * f + py * off;
        if (k === 0) ctx.moveTo(lx, ly);
        else ctx.lineTo(lx, ly);
      }
      ctx.stroke();

      ctx.fillStyle = p.raised;
      ctx.strokeStyle = hot ? p.red : p.ink;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      if (s.anchor === "gw") ctx.rect(nx - 6, ny - 6, 12, 12);
      else ctx.arc(nx, ny, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      const name = s.label.replace(/^RTT to /, "");
      const last = store.traces.get(s.name)?.last();
      const below = Math.sin(ang) > 0.2;
      const ty = below ? ny + 12 : ny - 30;
      ctx.textAlign = Math.abs(Math.cos(ang)) < 0.3 ? "center" : Math.cos(ang) > 0 ? "left" : "right";
      const tx = ctx.textAlign === "center" ? nx : nx + Math.sign(Math.cos(ang)) * 10;
      ctx.textBaseline = "top";
      const value = last === null || last === undefined ? "no reply" : `${last.toFixed(2)} ms`;
      setFont(ctx, 12, 600);
      backdrop(ctx, name, tx, ty, 15, p.paper);
      ctx.fillStyle = hot ? p.red : p.ink;
      ctx.fillText(name, tx, ty);
      setFont(ctx, 11, 460, "condensed");
      backdrop(ctx, value, tx, ty + 15, 13, p.paper);
      ctx.fillStyle = p.inkSoft;
      ctx.fillText(value, tx, ty + 15);
    });

    ctx.fillStyle = p.ink;
    ctx.beginPath();
    ctx.roundRect(cx - 15, cy - 9, 30, 18, 3);
    ctx.fill();
    setFont(ctx, 11, 600);
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    const label = rtt.length ? "This laptop" : "This laptop: no probe anchors";
    backdrop(ctx, label, cx + 20, cy + 12, 14, p.paper);
    ctx.fillStyle = p.inkSoft;
    ctx.fillText(label, cx + 20, cy + 12);
  });

  return (
    <canvas
      ref={ref}
      className="linkmap"
      role="img"
      aria-label="Probe paths from this laptop to each anchor; a trembling path is being disturbed."
    />
  );
}
