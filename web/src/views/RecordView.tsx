import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { fmtDuration } from "../lib/format";
import { useLive } from "../lib/store";
import { attempt, toast } from "../lib/toast";

const LABELS = [
  { id: "empty", text: "Empty", hint: "Nobody in the home, or nobody near the laptop's links" },
  { id: "still", text: "Still", hint: "Someone present but sitting or lying still" },
  { id: "walking", text: "Walking", hint: "Someone moving around normally" },
];

const PROTOCOL = [
  { label: "empty", seconds: 120 },
  { label: "still", seconds: 120 },
  { label: "walking", seconds: 120 },
];

interface Setup {
  laptop: string;
  router: string;
  people: string;
  notes: string;
  moved: boolean;
}

export function RecordView() {
  const live = useLive();
  const rec = live.last?.rec;
  const kind = live.hello?.source?.kind;
  const [setup, setSetup] = useState<Setup>({ laptop: "", router: "", people: "1", notes: "", moved: false });
  const [run, setRun] = useState<{ step: number; until: number } | null>(null);
  const [, setTick] = useState(0);
  const timer = useRef<number | null>(null);

  // Guided protocol: switch labels on a timer while recording.
  useEffect(() => {
    if (!run) return;
    timer.current = window.setInterval(() => {
      setTick((x) => x + 1);
      if (Date.now() < run.until) return;
      const next = run.step + 1;
      if (next >= PROTOCOL.length) {
        setRun(null);
        void attempt(() => api.post("/api/record/label", { label: null }));
        toast("Protocol finished. Stop the recording when you are ready.");
      } else {
        void attempt(() => api.post("/api/record/label", { label: PROTOCOL[next].label }));
        setRun({ step: next, until: Date.now() + PROTOCOL[next].seconds * 1000 });
      }
    }, 250);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [run]);

  const start = () =>
    attempt(
      () =>
        api.post("/api/record/start", {
          setup: { ...setup, people: Number(setup.people) || 0 },
          tag: kind === "sim" ? "sim" : "",
        }),
      "Recording started",
    );
  const stop = async () => {
    setRun(null);
    const out = await attempt(() => api.post<{ id?: string }>("/api/record/stop"));
    if (out?.id) toast(`Saved session ${out.id}`);
  };
  const label = (l: string | null) => attempt(() => api.post("/api/record/label", { label: l }));
  const runProtocol = async () => {
    await label(PROTOCOL[0].label);
    setRun({ step: 0, until: Date.now() + PROTOCOL[0].seconds * 1000 });
  };

  return (
    <div className="page">
      <header className="page-head">
        <h2>Record labelled data</h2>
        <p>
          The classifier and every honest number in the report come from recordings like these. Repeat
          each label at different times of day and on different days, and record at least once after
          moving the laptop or router. The cross-day score is the one that counts.
        </p>
      </header>

      {!rec ? (
        <section className="sheet sheet-pad record-setup">
          <h3>Session setup</h3>
          <div className="form-grid">
            <label className="field">
              <span>Where is the laptop?</span>
              <input className="input" value={setup.laptop} placeholder="Bedroom desk" onChange={(e) => setSetup({ ...setup, laptop: e.target.value })} />
            </label>
            <label className="field">
              <span>Where is the router?</span>
              <input className="input" value={setup.router} placeholder="Living room shelf" onChange={(e) => setSetup({ ...setup, router: e.target.value })} />
            </label>
            <label className="field">
              <span>People at home</span>
              <input className="input" inputMode="numeric" value={setup.people} onChange={(e) => setSetup({ ...setup, people: e.target.value })} />
            </label>
            <label className="field wide">
              <span>Notes (fans, open windows, others streaming)</span>
              <input className="input" value={setup.notes} onChange={(e) => setSetup({ ...setup, notes: e.target.value })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={setup.moved} onChange={(e) => setSetup({ ...setup, moved: e.target.checked })} />
              I moved the laptop or router since the last session
            </label>
          </div>
          <button className="btn btn-primary" disabled={kind === "replay"} onClick={start}>
            Start recording
          </button>
          {kind === "sim" && <p className="caption">This will record the simulator. Simulated sessions are tagged and kept apart in evaluation.</p>}
        </section>
      ) : (
        <section className="sheet sheet-pad recording" aria-live="polite">
          <div className="rec-head">
            <span className="rec-dot" aria-hidden="true" />
            <h3>
              Recording <span className="num">{fmtDuration(rec.elapsed)}</span>
            </h3>
            <span className="caption num">{rec.n.toLocaleString()} samples</span>
            <button className="btn btn-danger" onClick={stop}>
              Stop and save
            </button>
          </div>

          <p className="label-now">
            Current label: <strong>{rec.label ?? "none"}</strong>
          </p>
          <div className="label-buttons" role="group" aria-label="Set the current label">
            {LABELS.map((l) => (
              <button key={l.id} className="label-btn" aria-pressed={rec.label === l.id} onClick={() => label(l.id)} disabled={!!run}>
                <strong>{l.text}</strong>
                <span>{l.hint}</span>
              </button>
            ))}
            <button className="label-btn" aria-pressed={rec.label === null} onClick={() => label(null)} disabled={!!run}>
              <strong>No label</strong>
              <span>Transitions and anything ambiguous</span>
            </button>
          </div>

          <div className="protocol">
            {run ? (
              <>
                <p>
                  Guided protocol, step {run.step + 1} of {PROTOCOL.length}: <strong>{PROTOCOL[run.step].label}</strong> for another{" "}
                  <span className="num">{Math.max(0, Math.ceil((run.until - Date.now()) / 1000))} s</span>.
                </p>
                <button className="btn btn-quiet" onClick={() => setRun(null)}>
                  Stop protocol
                </button>
              </>
            ) : (
              <>
                <p className="caption">Or let the protocol switch labels for you: empty, still, then walking, two minutes each.</p>
                <button className="btn" onClick={runProtocol}>
                  Run guided protocol
                </button>
              </>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
