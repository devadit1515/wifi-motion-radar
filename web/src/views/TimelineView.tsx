import { useEffect, useState } from "react";
import { EventList } from "../components/EventList";
import { api } from "../lib/api";
import { palette, setFont, useCanvasLoop } from "../lib/canvas";
import { fmtClock } from "../lib/format";
import type { Guard, RadarEvent } from "../lib/protocol";
import { store, useLive } from "../lib/store";
import { attempt } from "../lib/toast";

interface Minute {
  minute: number;
  motion: number;
  z_max: number;
  open?: boolean;
}

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function TimelineView() {
  const live = useLive();
  const [day, setDay] = useState(() => new Date());
  const [rows, setRows] = useState<Minute[]>([]);
  const [source, setSource] = useState("live");
  const [events, setEvents] = useState<RadarEvent[]>([]);

  useEffect(() => {
    const load = () => {
      void api.get<{ source: string; minutes: Minute[] }>(`/api/timeline?date=${isoDay(day)}`).then((r) => {
        setRows(r.minutes);
        setSource(r.source);
      }).catch(() => undefined);
      void api.get<RadarEvent[]>("/api/events?n=200").then((e) => setEvents([...e].reverse())).catch(() => undefined);
    };
    load();
    const id = window.setInterval(load, 15000);
    return () => window.clearInterval(id);
  }, [day]);

  const active = rows.filter((r) => r.motion > 0.05);
  const isToday = isoDay(day) === isoDay(new Date());
  const shift = (d: number) => setDay(new Date(day.getFullYear(), day.getMonth(), day.getDate() + d));

  return (
    <div className="page">
      <header className="page-head">
        <h2>Timeline</h2>
        <p>When there was motion, minute by minute. Simulated and replayed data are kept out of this history.</p>
      </header>

      <section className="sheet sheet-pad">
        <div className="day-nav">
          <button className="btn btn-quiet" onClick={() => shift(-1)} aria-label="Previous day">Previous day</button>
          <h3>{day.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</h3>
          <button className="btn btn-quiet" onClick={() => shift(1)} disabled={isToday} aria-label="Next day">Next day</button>
        </div>
        <DayStrip rows={rows} />
        <p className="caption">
          {source !== "live"
            ? "The engine is not on live WiFi right now, so this shows only the current run, held in memory."
            : active.length
              ? `Motion in ${active.length} minute${active.length === 1 ? "" : "s"}, first at ${fmtClock(active[0].minute)}, last at ${fmtClock(active[active.length - 1].minute)}.`
              : rows.length
                ? "Recorded, with no motion on this day."
                : "No recording for this day. The timeline fills in while the engine runs on live WiFi."}
        </p>
      </section>

      <div className="two-col">
        <GuardCard guard={live.hello?.guard ?? null} />
        <section className="sheet sheet-pad">
          <h3>History</h3>
          <EventList events={events} limit={30} />
        </section>
      </div>
    </div>
  );
}

function DayStrip({ rows }: { rows: Minute[] }) {
  const ref = useCanvasLoop((ctx, w, h) => {
    const p = palette();
    const L = 0;
    const plotW = w;
    const barH = h - 22;
    const dayStart = new Date();
    if (rows.length) {
      const d = new Date(rows[0].minute * 1000);
      dayStart.setFullYear(d.getFullYear(), d.getMonth(), d.getDate());
    }
    dayStart.setHours(0, 0, 0, 0);
    const t0 = dayStart.getTime() / 1000;
    const colW = plotW / 1440;
    ctx.fillStyle = p.grid;
    ctx.fillRect(L, 0, plotW, barH);
    for (const r of rows) {
      const m = Math.floor((r.minute - t0) / 60);
      if (m < 0 || m >= 1440) continue;
      ctx.fillStyle = p.raised;
      ctx.fillRect(L + m * colW, 0, Math.max(colW, 1), barH);
      if (r.motion > 0) {
        ctx.globalAlpha = Math.min(1, 0.25 + r.motion);
        ctx.fillStyle = p.red;
        const hh = barH * Math.min(1, 0.3 + r.motion);
        ctx.fillRect(L + m * colW, barH - hh, Math.max(colW, 1.2), hh);
        ctx.globalAlpha = 1;
      }
    }
    setFont(ctx, 11, 480);
    ctx.fillStyle = p.inkSoft;
    ctx.textBaseline = "bottom";
    for (let hr = 0; hr <= 24; hr += 3) {
      const x = L + hr * 60 * colW;
      ctx.textAlign = hr === 0 ? "left" : hr === 24 ? "right" : "center";
      ctx.fillText(`${String(hr).padStart(2, "0")}:00`, x, h);
      ctx.fillStyle = p.gridMajor;
      ctx.fillRect(Math.round(x), 0, 1, barH);
      ctx.fillStyle = p.inkSoft;
    }
  });
  return (
    <canvas
      ref={ref}
      className="daystrip"
      role="img"
      aria-label="Motion by minute over the day. Pale paper means recorded, red marks motion, grey means not recorded."
    />
  );
}

function GuardCard({ guard }: { guard: Guard | null }) {
  const [hook, setHook] = useState(guard?.webhook ?? "");
  const [perm, setPerm] = useState(() => ("Notification" in window ? Notification.permission : "denied"));
  const armed = guard?.armed ?? false;

  const set = async (body: Partial<Guard>) => {
    const g = await attempt(() => api.post<Guard>("/api/guard", body), body.armed === undefined ? "Saved" : body.armed ? "Away Guard armed" : "Away Guard disarmed");
    if (g && store.hello) {
      store.hello.guard = g;
    }
  };

  return (
    <section className={armed ? "sheet sheet-pad guard armed" : "sheet sheet-pad guard"} aria-labelledby="guard-title">
      <h3 id="guard-title">Away Guard</h3>
      <p>
        Arm it when you leave. If motion starts while it is armed, this page shows a notification, and an
        optional phone alert is sent through a webhook.
      </p>
      <button className={armed ? "btn btn-danger" : "btn btn-primary"} onClick={() => set({ armed: !armed })}>
        {armed ? "Disarm" : "Arm Away Guard"}
      </button>
      {perm !== "granted" && "Notification" in window && (
        <button className="btn btn-quiet" onClick={() => void Notification.requestPermission().then(setPerm)}>
          Allow browser notifications
        </button>
      )}
      <label className="field">
        <span>Phone alerts: an ntfy.sh topic URL, or any URL that accepts a POST</span>
        <input className="input" value={hook} placeholder="https://ntfy.sh/your-private-topic" onChange={(e) => setHook(e.target.value)} />
      </label>
      <button className="btn" onClick={() => set({ webhook: hook })}>
        Save alert URL
      </button>
      <p className="caption">Alerts are rate limited to one every two minutes. The URL is stored only on this machine.</p>
    </section>
  );
}
