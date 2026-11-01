import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useLive } from "../lib/store";
import { ProbeResultCard } from "./ProbeView";

interface SessionSummary {
  id: string;
  created: number;
  duration: number;
  source: string;
  label_seconds: Record<string, number>;
}

const LABELS = ["empty", "still", "walking"];
const TARGET_MIN = 10; // per label per day before a day is useful for cross-day testing

export function LabView() {
  const live = useLive();
  const [lab, setLab] = useState<{ available?: boolean } | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);

  useEffect(() => {
    void api.get<{ available?: boolean }>("/api/lab").then(setLab).catch(() => setLab({ available: false }));
    void api.get<SessionSummary[]>("/api/sessions").then(setSessions).catch(() => undefined);
  }, []);

  const real = sessions.filter((s) => s.source !== "sim");
  const byDay = new Map<string, Record<string, number>>();
  for (const s of real) {
    const day = new Date(s.created * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
    const row = byDay.get(day) ?? {};
    for (const [k, v] of Object.entries(s.label_seconds)) row[k] = (row[k] ?? 0) + v / 60;
    byDay.set(day, row);
  }
  const readyDays = [...byDay.values()].filter((r) => LABELS.every((l) => (r[l] ?? 0) >= TARGET_MIN)).length;

  return (
    <div className="page">
      <header className="page-head">
        <h2>Lab</h2>
        <p>
          Where the honest numbers will live: classifier accuracy within a session, across days, and after
          moving the laptop or router, each against two baselines it has to beat.
        </p>
      </header>

      {lab?.available === false && (
        <section className="sheet sheet-pad">
          <h3>No evaluation yet</h3>
          <p>
            Evaluation needs labelled recordings from at least two different days, so one day can be held
            out entirely for testing. A model that only works on the day it was trained on is not reported
            as working.
          </p>
          <ul className="plain-list">
            <li>Within-session split: reported, but flagged as flattering.</li>
            <li>Leave-one-day-out: the headline number.</li>
            <li>After moving the laptop or router: reported before and after recalibration.</li>
            <li>Baselines: always predicting empty, and a single variance threshold.</li>
          </ul>
        </section>
      )}

      <section className="sheet sheet-pad">
        <h3>Dataset so far</h3>
        <p className="caption">
          Minutes of live, labelled recording per day. A day becomes useful for cross-day testing once every
          label has {TARGET_MIN} minutes. Days ready: <strong>{readyDays}</strong> of the 2 needed.
        </p>
        {byDay.size === 0 ? (
          <p>
            No live recordings yet. <a href="#/record">Record a session</a> or <a href="#/probe">run the probe</a>.
          </p>
        ) : (
          <table className="table coverage">
            <thead>
              <tr>
                <th scope="col">Day</th>
                {LABELS.map((l) => (
                  <th key={l} scope="col" className="r">
                    {l}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...byDay.entries()].map(([day, row]) => (
                <tr key={day}>
                  <td>{day}</td>
                  {LABELS.map((l) => {
                    const m = row[l] ?? 0;
                    return (
                      <td key={l} className={m >= TARGET_MIN ? "r num ok" : "r num"}>
                        {m.toFixed(1)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {live.lastProbe && <ProbeResultCard result={live.lastProbe} />}
    </div>
  );
}
