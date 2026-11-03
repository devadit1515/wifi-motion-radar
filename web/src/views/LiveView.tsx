import { useMemo, useState } from "react";
import { EventList } from "../components/EventList";
import { FloorPlan } from "../components/FloorPlan";
import { LinkMap } from "../components/LinkMap";
import { Recorder, type Lane } from "../components/Recorder";
import { StateWord } from "../components/StateWord";
import { Waterfall } from "../components/Waterfall";
import { api } from "../lib/api";
import { fmtDuration } from "../lib/format";
import type { StreamInfo } from "../lib/protocol";
import { useLive } from "../lib/store";
import { attempt } from "../lib/toast";

const ORDER = ["sonar", "sonar_share", "sonar_range", "sonar_bearing", "rtt", "retry", "rssi", "rate", "fail", "quality", "tx"];
const KEY_KINDS = new Set(["sonar", "sonar_range", "rtt", "retry", "rssi"]);

function toLane(s: StreamInfo): Lane {
  return { stream: s.name, label: s.label, unit: s.unit, tol: s.stab_tol };
}

export function LiveView() {
  const live = useLive();
  const hello = live.hello;
  const zOn = hello?.detector.z_on ?? 3;
  const [windowS, setWindowS] = useState(30);
  const [all, setAll] = useState(false);

  const lanes = useMemo(() => {
    const usable = live.streams.filter((s) => s.kind !== "truth" && (all || KEY_KINDS.has(s.kind)));
    usable.sort((a, b) => {
      const k = ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind);
      if (k) return k;
      if (a.anchor === "gw") return -1;
      if (b.anchor === "gw") return 1;
      return a.name.localeCompare(b.name);
    });
    return usable.map(toLane);
  }, [live.streams, all]);

  const source = hello?.source;
  const specStream = live.spec.stream ? live.info(live.spec.stream)?.label : null;
  const hot = new Set(
    Object.entries(live.streamState)
      .filter(([n, s]) => n.startsWith("rtt.") && (s.z ?? 0) > zOn)
      .map(([n]) => n.slice(4)),
  );

  return (
    <div className="live">
      {source?.kind === "replay" && <ReplayBar />}
      <section className="live-head">
        <StateWord zOn={zOn} calibS={hello?.detector.calib_s ?? 20} />
        <div className="live-map">
          <LinkMap zOn={zOn} />
          <p className="caption">
            Each line is a probe path from this laptop to one anchor. It trembles when that path is
            disturbed. Directions on this map mean nothing: the system does not know where anchors are.
          </p>
        </div>
      </section>

      <section className="sheet recorder-sheet" aria-labelledby="rec-title">
        <header className="sheet-head">
          <h2 id="rec-title">Recorder</h2>
          <div className="sheet-tools">
            <div className="segmented" role="group" aria-label="Time window">
              {[15, 30, 60].map((s) => (
                <button key={s} aria-pressed={windowS === s} onClick={() => setWindowS(s)}>
                  {s} s
                </button>
              ))}
            </div>
            <div className="segmented" role="group" aria-label="Streams shown">
              <button aria-pressed={!all} onClick={() => setAll(false)}>Key streams</button>
              <button aria-pressed={all} onClick={() => setAll(true)}>All streams</button>
            </div>
            <button className="btn btn-quiet" onClick={() => attempt(() => api.post("/api/calibrate"), "Relearning the baseline")}>
              Relearn baseline
            </button>
          </div>
        </header>
        <Recorder lanes={lanes} windowS={windowS} zOn={zOn} title="Scrolling chart of every sensing stream" />
        <p className="caption recorder-note">
          Blue pen: fused motion score against its threshold (dashed). Red wash: motion, or a stream above
          threshold. Red ticks: lost probes. Each lane auto-scales to its own recent range.
        </p>
      </section>

      <div className="live-grid">
        <section className="sheet sheet-pad" aria-labelledby="spec-title">
          <h3 id="spec-title">Spectrum</h3>
          <p className="caption">{specStream ? `${specStream}, last 2 s window per column` : "Primary fast stream"}</p>
          <Waterfall />
        </section>
        <section className="sheet sheet-pad" aria-labelledby="ev-title">
          <h3 id="ev-title">Events</h3>
          <EventList events={live.events} />
        </section>
      </div>

      {source?.kind === "sim" && source.layout && (
        <section className="sheet sheet-pad sim" aria-labelledby="sim-title">
          <div className="sim-text">
            <h3 id="sim-title">Simulated home</h3>
            <p className="caption">
              Ground truth from the physics simulator. The detector never sees this map; it only gets
              the same timing and signal streams a real laptop would.
            </p>
            <SimControls />
            {live.last?.truth?.load && (
              <p className="caption">Someone else is streaming video right now. Watch round trips rise with nobody moving.</p>
            )}
          </div>
          <FloorPlan layout={source.layout} truth={live.last?.truth} hotAnchors={hot} />
        </section>
      )}
    </div>
  );
}

function SimControls() {
  const live = useLive();
  const mode = live.last?.truth?.mode ?? "auto";
  const set = (m: string) => attempt(() => api.post("/api/sim", { mode: m }));
  return (
    <div className="sim-controls">
      <div className="segmented" role="group" aria-label="Simulated person">
        {[
          ["auto", "Scripted day"],
          ["empty", "Away"],
          ["still", "Sitting still"],
          ["walking", "Walking"],
        ].map(([m, label]) => (
          <button key={m} aria-pressed={mode === m} onClick={() => set(m)}>
            {label}
          </button>
        ))}
      </div>
      <p className="caption">
        The simulated person is <strong>{live.last?.truth?.state ?? "…"}</strong>.
      </p>
    </div>
  );
}

function ReplayBar() {
  const live = useLive();
  const r = live.replay;
  if (!r) return <div className="replay-bar sheet">Loading replay…</div>;
  const ctl = (body: Record<string, unknown>) => attempt(() => api.post("/api/replay", body));
  return (
    <div className="replay-bar sheet" role="region" aria-label="Replay controls">
      <button className="btn" onClick={() => ctl({ paused: !r.paused })}>
        {r.paused ? "Play" : "Pause"}
      </button>
      <div className="segmented" role="group" aria-label="Playback speed">
        {[1, 4, 8].map((s) => (
          <button key={s} aria-pressed={r.speed === s} onClick={() => ctl({ speed: s })}>
            {s}×
          </button>
        ))}
      </div>
      <input
        className="scrubber"
        type="range"
        min={0}
        max={r.duration}
        step={0.5}
        value={r.t}
        aria-label="Replay position"
        onChange={(e) => ctl({ seek: Number(e.target.value) })}
      />
      <span className="num">
        {fmtDuration(r.t)} / {fmtDuration(r.duration)}
      </span>
      <span className="caption">Label here: {r.label ?? "none"}</span>
      <button className="btn btn-quiet" onClick={() => attempt(() => api.post("/api/source", { kind: "live" }))}>
        Back to live
      </button>
    </div>
  );
}
