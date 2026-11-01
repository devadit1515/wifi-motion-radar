import { useEffect, useState } from "react";
import { api } from "./lib/api";
import { fmtDuration } from "./lib/format";
import { useLive } from "./lib/store";
import { attempt, useToasts } from "./lib/toast";
import { LabView } from "./views/LabView";
import { LiveView } from "./views/LiveView";
import { ProbeView } from "./views/ProbeView";
import { RecordView } from "./views/RecordView";
import { SessionsView } from "./views/SessionsView";
import { SystemView } from "./views/SystemView";
import { TimelineView } from "./views/TimelineView";

const VIEWS = [
  { id: "live", label: "Live", el: LiveView },
  { id: "timeline", label: "Timeline", el: TimelineView },
  { id: "probe", label: "Probe", el: ProbeView },
  { id: "record", label: "Record", el: RecordView },
  { id: "sessions", label: "Sessions", el: SessionsView },
  { id: "lab", label: "Lab", el: LabView },
  { id: "system", label: "System", el: SystemView },
] as const;

function useRoute(): string {
  const read = () => location.hash.replace(/^#\/?/, "") || "live";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function App() {
  const route = useRoute();
  const view = VIEWS.find((v) => v.id === route) ?? VIEWS[0];
  const View = view.el;
  useEffect(() => {
    document.title = view.id === "live" ? "Motion Radar" : `${view.label} | Motion Radar`;
  }, [view]);

  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <TopBar route={view.id} />
      <main id="main" className="main">
        <View />
      </main>
      <Toasts />
    </>
  );
}

function TopBar({ route }: { route: string }) {
  const live = useLive();
  const kind = live.hello?.source?.kind ?? "none";
  const rec = live.last?.rec;
  const armed = live.hello?.guard.armed;
  const probe = live.last?.probe;
  const motion = live.state() === "motion";

  const switchTo = (k: string) => {
    if (k === kind) return;
    void attempt(() => api.post("/api/source", { kind: k }), k === "live" ? "Switched to live WiFi" : "Switched to the simulator");
  };

  return (
    <header className="topbar">
      <a className="brand" href="#/live" aria-label="Motion Radar, live view">
        <svg viewBox="0 0 40 20" aria-hidden="true" className={motion ? "brand-mark hot" : "brand-mark"}>
          <path d="M1 12h7l3-8 5 14 4-11 3 6h16" />
        </svg>
        <span>Motion Radar</span>
      </a>
      <nav className="tabs" aria-label="Views">
        {VIEWS.map((v) => (
          <a key={v.id} href={`#/${v.id}`} aria-current={route === v.id ? "page" : undefined}>
            {v.label}
            {v.id === "probe" && probe && probe.stage !== "done" && <span className="tab-dot" aria-label="running" />}
          </a>
        ))}
      </nav>
      <div className="status">
        {rec && (
          <a className="pill pill-rec" href={rec.probe ? "#/probe" : "#/record"}>
            <span className="rec-dot" aria-hidden="true" />
            Recording <span className="num">{fmtDuration(rec.elapsed)}</span>
          </a>
        )}
        {armed && (
          <a className="pill" href="#/timeline">
            Away Guard on
          </a>
        )}
        {kind === "replay" ? (
          <span className="pill">Replaying</span>
        ) : (
          <div className="segmented" role="group" aria-label="Data source">
            <button aria-pressed={kind === "live"} onClick={() => switchTo("live")}>
              Live WiFi
            </button>
            <button aria-pressed={kind === "sim"} onClick={() => switchTo("sim")}>
              Simulator
            </button>
          </div>
        )}
        <span className={`conn conn-${live.conn}`} role="status" title={live.conn === "open" ? "Connected to the engine" : "Reconnecting to the engine"}>
          <span className="sr-only">{live.conn === "open" ? "Connected" : "Reconnecting"}</span>
        </span>
      </div>
    </header>
  );
}

function Toasts() {
  const toasts = useToasts();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <p key={t.id} className={t.tone === "error" ? "toast toast-error" : "toast"}>
          {t.text}
        </p>
      ))}
    </div>
  );
}
