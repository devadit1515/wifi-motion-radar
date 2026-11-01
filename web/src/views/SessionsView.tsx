import { useCallback, useEffect, useState } from "react";
import { palette, setFont, useCanvasLoop } from "../lib/canvas";
import { api } from "../lib/api";
import { fmtDate, fmtDuration } from "../lib/format";
import { quantile } from "../lib/ring";
import { attempt } from "../lib/toast";

interface SessionSummary {
  id: string;
  created: number;
  duration: number;
  n_samples: number;
  source: string;
  streams: string[];
  label_seconds: Record<string, number>;
  setup: Record<string, unknown>;
}

interface LabelSpan {
  start: number;
  end: number;
  label: string;
}

interface Overview {
  meta: { id: string; duration: number; labels: LabelSpan[]; streams: { name: string; label: string; kind: string }[] };
  streams: Record<string, { t: number[]; lo: (number | null)[]; hi: (number | null)[] }>;
}

export function SessionsView() {
  const [list, setList] = useState<SessionSummary[] | null>(null);
  const [sel, setSel] = useState<string | null>(null);

  const load = useCallback(async () => {
    const out = await attempt(() => api.get<SessionSummary[]>("/api/sessions"));
    if (out) setList([...out].reverse());
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="page">
      <header className="page-head">
        <h2>Sessions</h2>
        <p>Every recording and probe run. Open one to see the whole session, fix its labels, or replay it through the live pipeline.</p>
      </header>
      {list === null ? (
        <p className="caption">Loading…</p>
      ) : list.length === 0 ? (
        <section className="sheet sheet-pad empty-state">
          <h3>No recordings yet</h3>
          <p>Record a labelled session or run the feasibility probe; both save here.</p>
          <p>
            <a href="#/record">Record a session</a> or <a href="#/probe">run the probe</a>.
          </p>
        </section>
      ) : (
        <div className="sessions">
          <ul className="session-list" aria-label="Recorded sessions">
            {list.map((s) => (
              <li key={s.id}>
                <button className="session-item" aria-pressed={sel === s.id} onClick={() => setSel(s.id)}>
                  <span className="session-date">{fmtDate(s.created)}</span>
                  <span className="caption">
                    {fmtDuration(s.duration)}
                    {s.source === "sim" ? ", simulated" : ""}
                    {s.setup?.purpose ? `, ${String(s.setup.purpose)}` : ""}
                  </span>
                  <LabelBar seconds={s.label_seconds} total={s.duration} />
                </button>
              </li>
            ))}
          </ul>
          {sel ? <SessionDetail key={sel} id={sel} onDeleted={() => { setSel(null); void load(); }} /> : (
            <p className="caption session-hint">Choose a session to open it.</p>
          )}
        </div>
      )}
    </div>
  );
}

function LabelBar({ seconds, total }: { seconds: Record<string, number>; total: number }) {
  const entries = Object.entries(seconds);
  if (!entries.length) return <span className="caption">No labels</span>;
  return (
    <span className="label-bar" aria-label={entries.map(([k, v]) => `${k} ${Math.round(v)} s`).join(", ")}>
      {entries.map(([k, v]) => (
        <span key={k} className={`lb lb-${k}`} style={{ width: `${(100 * v) / Math.max(total, 1)}%` }} />
      ))}
    </span>
  );
}

function SessionDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const [ov, setOv] = useState<Overview | null>(null);
  const [labels, setLabels] = useState<LabelSpan[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    void attempt(() => api.get<Overview>(`/api/sessions/${id}/overview`)).then((o) => {
      if (o) {
        setOv(o);
        setLabels(o.meta.labels ?? []);
      }
    });
  }, [id]);

  if (!ov) return <p className="caption">Loading session…</p>;

  const replay = () =>
    attempt(() => api.post("/api/source", { kind: "replay", session: id }), "Replaying in the Live view").then((r) => {
      if (r) location.hash = "#/live";
    });
  const remove = async () => {
    if (!window.confirm(`Delete session ${id}? The recording is removed from disk.`)) return;
    const ok = await attempt(() => api.del(`/api/sessions/${id}`), "Session deleted");
    if (ok) onDeleted();
  };
  const save = async () => {
    const ok = await attempt(() => api.patch(`/api/sessions/${id}`, { labels }), "Labels saved");
    if (ok) setDirty(false);
  };

  return (
    <section className="sheet sheet-pad session-detail" aria-labelledby="sd-title">
      <header className="result-head">
        <h3 id="sd-title">{id}</h3>
        <div className="sheet-tools">
          <button className="btn btn-primary" onClick={replay}>Replay</button>
          <button className="btn btn-danger" onClick={remove}>Delete</button>
        </div>
      </header>
      <SessionChart ov={ov} labels={labels} />
      <h3 className="sub">Labels</h3>
      {labels.length === 0 && <p className="caption">This session has no labels.</p>}
      <ul className="label-edit">
        {labels.map((l, i) => (
          <li key={`${l.start}-${i}`}>
            <span className="num">{fmtDuration(l.start)}–{fmtDuration(l.end)}</span>
            <select
              className="input"
              value={l.label}
              aria-label={`Label for ${fmtDuration(l.start)} to ${fmtDuration(l.end)}`}
              onChange={(e) => {
                setLabels(labels.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)));
                setDirty(true);
              }}
            >
              {Array.from(new Set(["empty", "still", "walking", l.label])).map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
            <button
              className="btn btn-quiet"
              onClick={() => {
                setLabels(labels.filter((_, j) => j !== i));
                setDirty(true);
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      {dirty && (
        <button className="btn btn-primary" onClick={() => void save()}>
          Save labels
        </button>
      )}
    </section>
  );
}

function SessionChart({ ov, labels }: { ov: Overview; labels: LabelSpan[] }) {
  const names = ov.meta.streams.filter((s) => s.kind !== "truth" && ov.streams[s.name]).map((s) => s);
  const laneH = 34;
  const top = 26;
  const height = top + names.length * laneH + 20;

  const ref = useCanvasLoop((ctx, w, h) => {
    const p = palette();
    const L = w < 640 ? 8 : 150;
    const plotW = w - L - 10;
    const D = Math.max(ov.meta.duration, 1);
    const x = (t: number) => L + (t / D) * plotW;
    for (const l of labels) {
      ctx.fillStyle = l.label === "walking" ? p.red : l.label === "still" ? p.inkSoft : p.gridMajor;
      ctx.fillRect(x(l.start), 6, Math.max(1, x(l.end) - x(l.start)), 12);
    }
    setFont(ctx, 11, 520);
    names.forEach((s, i) => {
      const y0 = top + i * laneH;
      const d = ov.streams[s.name];
      const vals = [...d.lo, ...d.hi].filter((v): v is number => v !== null).sort((a, b) => a - b);
      let lo = quantile(vals, 0.01);
      let hi = quantile(vals, 0.99);
      if (!(hi > lo)) {
        lo -= 1;
        hi += 1;
      }
      const y = (v: number) => y0 + laneH - 4 - ((v - lo) / (hi - lo)) * (laneH - 8);
      ctx.strokeStyle = p.rule;
      ctx.beginPath();
      ctx.moveTo(0, y0 + 0.5);
      ctx.lineTo(w, y0 + 0.5);
      ctx.stroke();
      ctx.strokeStyle = p.ink;
      ctx.lineWidth = 1;
      ctx.beginPath();
      d.t.forEach((t, k) => {
        const a = d.lo[k];
        const b = d.hi[k];
        if (a === null || b === null) return;
        const xx = Math.round(x(t)) + 0.5;
        ctx.moveTo(xx, y(Math.min(Math.max(a, lo), hi)));
        ctx.lineTo(xx, y(Math.min(Math.max(b, lo), hi)) - 0.5);
      });
      ctx.stroke();
      if (w >= 640) {
        ctx.fillStyle = p.ink;
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        ctx.fillText(s.label, 10, y0 + laneH / 2);
      }
    });
    ctx.fillStyle = p.inkSoft;
    ctx.textBaseline = "bottom";
    ctx.textAlign = "left";
    ctx.fillText("0:00", L, h);
    ctx.textAlign = "right";
    ctx.fillText(fmtDuration(D), L + plotW, h);
  });

  return <canvas ref={ref} style={{ width: "100%", height }} role="img" aria-label="Whole-session overview with label strip" />;
}
