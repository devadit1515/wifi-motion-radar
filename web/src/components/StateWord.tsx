import { useEffect, useRef } from "react";
import { prefersReducedMotion } from "../lib/canvas";
import { fmtAgo } from "../lib/format";
import { useLive, store } from "../lib/store";

const WORD = { calibrating: "Learning", empty: "Quiet", settled: "Quiet", motion: "Motion" } as const;

/**
 * The state, set as one word whose width and weight follow the fused motion score:
 * narrow and light when the house is still, wide and heavy when something moves.
 */
export function StateWord({ zOn, calibS }: { zOn: number; calibS: number }) {
  const live = useLive();
  const ref = useRef<HTMLHeadingElement>(null);
  const state = live.state();
  const det = live.last?.det;

  useEffect(() => {
    let raf = 0;
    let zs = 0;
    const reduce = prefersReducedMotion();
    const tick = () => {
      const el = ref.current;
      if (el) {
        const s = store.state();
        const z = store.last?.det.z ?? 0;
        const target = s === "motion" ? Math.max(z, zOn * 1.6) : s === "calibrating" ? 0 : z;
        zs = reduce ? target : zs + (target - zs) * 0.07;
        const k = Math.min(1, Math.max(0, zs / (zOn * 2.2)));
        el.style.fontStretch = `${(78 + 47 * k).toFixed(1)}%`;
        el.style.fontWeight = String(Math.round(320 + 520 * k));
        el.style.letterSpacing = `${(-0.035 + 0.02 * k).toFixed(3)}em`;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [zOn]);

  let line: string;
  if (state === "calibrating") {
    const left = Math.max(0, Math.ceil(calibS * (1 - (det?.calib ?? 0))));
    line = `Learning what this home looks like when nothing moves. Keep still or step out for ${left} s.`;
  } else if (state === "motion") {
    // Name the stream contributing most to the fused score, not merely the highest z.
    const top = Object.entries(live.streamState)
      .filter(([, s]) => s.z !== null && s.w > 0)
      .sort((a, b) => (b[1].z ?? 0) * b[1].w - (a[1].z ?? 0) * a[1].w)[0];
    const label = top ? live.info(top[0])?.label ?? top[0] : null;
    line = label ? `Something is moving. Strongest on ${label}.` : "Something is moving.";
  } else if (state === "settled") {
    line = `Motion stopped ${fmtAgo(det?.last_motion)}. Someone may still be here: WiFi timing cannot see a person who is not moving.`;
  } else {
    line = det?.last_motion
      ? `No motion since ${fmtAgo(det.last_motion)}.`
      : "No motion since the baseline was learned.";
  }

  return (
    <div className="state" aria-live="polite">
      <h1 ref={ref} className={`state-word state-${state}`}>
        {WORD[state]}
      </h1>
      <p className="state-line">{line}</p>
    </div>
  );
}
