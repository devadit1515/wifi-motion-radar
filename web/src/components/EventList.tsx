import { fmtClock } from "../lib/format";
import type { RadarEvent } from "../lib/protocol";

const TEXT: Record<string, string> = {
  motion_start: "Motion started",
  motion_end: "Motion stopped",
  empty: "Quiet for five minutes",
  calibrated: "Baseline learned",
  calibrate: "Relearning the baseline",
  guard_alert: "Away Guard alert",
  guard_armed: "Away Guard armed",
  guard_disarmed: "Away Guard disarmed",
  rec_start: "Recording started",
  rec_stop: "Recording saved",
  probe_done: "Feasibility probe finished",
  roam: "Roamed to another access point",
};

export function describeEvent(ev: RadarEvent): string {
  return TEXT[ev.kind] ?? ev.kind.replace(/_/g, " ");
}

export function EventList({ events, limit = 12 }: { events: RadarEvent[]; limit?: number }) {
  const shown = events.slice(0, limit);
  if (!shown.length) {
    return <p className="caption">Nothing yet. Events appear here as the detector changes state.</p>;
  }
  return (
    <ol className="events">
      {shown.map((ev, i) => (
        <li key={`${ev.wall}-${ev.kind}-${i}`} className={ev.kind.startsWith("motion_start") || ev.kind === "guard_alert" ? "ev hot" : "ev"}>
          <time className="num">{fmtClock(ev.wall)}</time>
          <span>{describeEvent(ev)}</span>
          {ev.z !== undefined && ev.z !== null && <span className="caption num">z {ev.z}</span>}
        </li>
      ))}
    </ol>
  );
}
