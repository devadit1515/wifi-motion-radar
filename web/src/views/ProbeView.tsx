import { Recorder } from "../components/Recorder";
import { api } from "../lib/api";
import { fmtDate, fmtNum } from "../lib/format";
import type { ProbeResult } from "../lib/protocol";
import { useLive } from "../lib/store";
import { attempt } from "../lib/toast";

const VERDICT_TEXT: Record<string, string> = {
  strong: "Motion is clearly visible from here.",
  usable: "Motion is visible, with some overlap with quiet periods.",
  weak: "Motion is only faintly visible from here.",
  none: "Motion is not visible from here yet.",
  "no data": "The probe did not collect enough data.",
};

export function ProbeView() {
  const live = useLive();
  const status = live.last?.probe;
  const protocol = live.hello?.protocol ?? [];
  const isReplay = live.hello?.source?.kind === "replay";

  if (status && status.stage !== "done") {
    const phase = status.stage === "phase" ? status.phase : status.next;
    const gw = live.streams.find((s) => s.anchor === "gw") ?? live.streams.find((s) => s.kind === "rtt");
    const lanes = gw ? [{ stream: gw.name, label: gw.label, unit: gw.unit, tol: gw.stab_tol }] : [];
    return (
      <div className="probe-layout">
        <div className="probe-run">
          <ol className="steps" aria-label="Probe steps">
            {protocol.map((p, i) => (
              <li key={p.label} aria-current={i === status.index ? "step" : undefined} className={i < status.index ? "done" : ""}>
                <span className="num">{i + 1}</span> {cap(p.label)} for {p.seconds} s
              </li>
            ))}
          </ol>
          <p className="probe-kicker">{status.stage === "lead_in" ? "Get ready" : `Step ${status.index + 1} of ${status.total}`}</p>
          <h1 className="probe-word">{cap(phase?.label ?? "")}</h1>
          <p className="probe-prompt">{phase?.prompt}</p>
          <p className="probe-count num" aria-live="polite">
            {Math.ceil(status.remaining ?? 0)}
            <span> s</span>
          </p>
          {status.settling && <p className="caption">Settling: the first 3 seconds of each step are not scored.</p>}
          <button className="btn btn-danger" onClick={() => attempt(() => api.post("/api/probe/cancel"), "Probe cancelled")}>
            Cancel probe
          </button>
        </div>
        <section className="sheet probe-live" aria-label="Live signal during the probe">
          <Recorder lanes={lanes} windowS={60} zOn={live.hello?.detector.z_on ?? 3} laneHeight={120} title="Motion score and router round trip during the probe" />
          <p className="caption recorder-note">What the probe is scoring right now: the fused motion score and the round trip to your router.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-head">
        <h2>Will it work in this home?</h2>
        <p>
          A two-minute test. You leave the room, then walk around. The probe measures how well each
          signal separates the two, and tunes the detector to the signals that work here. It also saves
          the run as a labelled recording.
        </p>
      </header>

      <div className="probe-setup">
        <section className="sheet sheet-pad">
          <h3>Before you start</h3>
          <ul className="plain-list">
            <li>Put the laptop one or two rooms away from the router, not next to it. A strong direct path drowns out the reflections that motion changes.</li>
            <li>Keep the laptop plugged in with the lid open so WiFi power saving stays off.</li>
            <li>Ask anyone else at home to stay still or out of the way for two minutes.</li>
          </ul>
        </section>
        <section className="sheet sheet-pad">
          <h3>What happens</h3>
          <ol className="plain-list">
            <li>A 5 second countdown.</li>
            {protocol.map((p) => (
              <li key={p.label}>
                {cap(p.label)} for {p.seconds} s. {p.prompt}
              </li>
            ))}
            <li>Results appear here, and the detector switches to the signals that worked.</li>
          </ol>
          <button
            className="btn btn-primary"
            disabled={isReplay}
            onClick={() => attempt(() => api.post("/api/probe/start", {}))}
          >
            Start probe
          </button>
          {isReplay && <p className="caption">Switch to Live or Simulator to run a probe.</p>}
        </section>
      </div>

      {live.lastProbe && <ProbeResultCard result={live.lastProbe} />}
    </div>
  );
}

export function ProbeResultCard({ result }: { result: ProbeResult }) {
  const live = useLive();
  const label = (s: string) => live.info(s)?.label ?? s;
  const best = result.best;
  const rows = Object.entries(result.streams);
  const d = result.detector;
  return (
    <section className="sheet sheet-pad probe-result" aria-labelledby="probe-res">
      <header className="result-head">
        <h3 id="probe-res">{VERDICT_TEXT[result.overall] ?? result.overall}</h3>
        <p className="caption">
          {fmtDate(result.wall_start)}
          {result.source === "sim" ? ", simulator" : result.source === "live" ? ", live WiFi" : ""}
          {result.session ? `, saved as ${result.session}` : ""}
        </p>
      </header>
      {best && (
        <p>
          Best signal: <strong>{label(best.stream)}</strong>, AUC {fmtNum(best.auc)}. When someone walks, its
          activity is {fmtNum(best.spread_ratio, 1)}× its quiet level.
        </p>
      )}
      {d.hit_rate !== null && (
        <p>
          The detector flagged <strong>{pct(d.hit_rate)}</strong> of walking windows and{" "}
          <strong className={d.false_alarm_rate && d.false_alarm_rate > 0.1 ? "motion-text" : ""}>
            {pct(d.false_alarm_rate)}
          </strong>{" "}
          of empty windows.
        </p>
      )}
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Signal</th>
            <th scope="col">Separation (AUC)</th>
            <th scope="col">Verdict</th>
            <th scope="col" className="r">Moving ÷ quiet</th>
            <th scope="col" className="r">New weight</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([s, r]) => (
            <tr key={s}>
              <td>{label(s)}</td>
              <td>
                <span className="auc-bar" aria-hidden="true">
                  <span style={{ width: `${Math.max(0, ((r.auc ?? 0.5) - 0.5) * 200)}%` }} className={`v-${r.verdict.replace(" ", "")}`} />
                </span>
                <span className="num">{fmtNum(r.auc)}</span>
              </td>
              <td>{r.verdict}</td>
              <td className="r num">{fmtNum(r.spread_ratio, 1)}×</td>
              <td className="r num">{fmtNum(r.weight)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="caption">
        AUC is the chance a walking window shows more activity than an empty one: 0.5 is a coin flip, 1.0
        is perfect. Windows overlap, so read it as a separability score for this home, not a significance
        test.
        {result.applied_weights === false && " No signal separated, so the detector kept its default weights."}
      </p>
    </section>
  );
}

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function pct(v: number | null): string {
  return v === null ? "–" : `${Math.round(v * 100)}%`;
}
