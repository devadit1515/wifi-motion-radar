import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { fmtNum } from "../lib/format";
import type { DetectorConfig, StreamInfo, StreamStats } from "../lib/protocol";
import { useLive } from "../lib/store";
import { attempt } from "../lib/toast";

interface Caps {
  source: Record<string, unknown> & { kind: string; parts?: Record<string, unknown>[] };
  streams: (StreamInfo & { stats: StreamStats | null })[];
  detector: { config: DetectorConfig; weights: Record<string, number> };
}

interface AnchorRow {
  id: string;
  ip: string;
  kind: string;
  name: string;
  enabled: boolean;
}

const KIND_TEXT: Record<string, string> = {
  gateway: "Router",
  infrastructure: "Mesh node or extender",
  client: "Other device",
};

export function SystemView() {
  const live = useLive();
  const [caps, setCaps] = useState<Caps | null>(null);
  const load = useCallback(() => void api.get<Caps>("/api/capabilities").then(setCaps).catch(() => undefined), []);
  useEffect(() => {
    load();
    const id = window.setInterval(load, 2000);
    return () => window.clearInterval(id);
  }, [load, live.hello]);

  const src = caps?.source;
  const wlan = src?.parts?.find((p) => p.name === "wlan");

  return (
    <div className="page">
      <header className="page-head">
        <h2>System</h2>
        <p>What this machine can actually sense, measured live. A stream polled 10 times a second that only changes every few seconds carries far less information than its poll rate suggests.</p>
      </header>

      <section className="sheet sheet-pad">
        <h3>Source</h3>
        {src ? (
          <dl className="facts">
            <dt>Mode</dt>
            <dd>{src.kind === "sim" ? "Physics simulator (no real WiFi)" : src.kind === "replay" ? `Replay of ${String(src.session)}` : "Live WiFi"}</dd>
            {wlan && (
              <>
                <dt>Adapter</dt>
                <dd>{String(wlan.adapter)}</dd>
                <dt>Network</dt>
                <dd>
                  {String(wlan.ssid)}, channel {String(wlan.channel)}
                </dd>
                <dt>Access point tag</dt>
                <dd className="num">{String(wlan.bssid_tag)} (a one-way hash; recordings never store the raw address)</dd>
                <dt>Windows 11 link-quality API</dt>
                <dd>{wlan.realtime_quality_api ? "available" : "not available"}</dd>
                <dt>Driver counters</dt>
                <dd>{wlan.driver_counters ? "available" : "not available"}</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="caption">Loading…</p>
        )}
      </section>

      <section className="sheet sheet-pad">
        <h3>Streams, measured over the last 10 s</h3>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Stream</th>
                <th scope="col" className="r">Expected</th>
                <th scope="col" className="r">Polled</th>
                <th scope="col" className="r">Changes / s</th>
                <th scope="col" className="r">Step</th>
                <th scope="col" className="r">Lost</th>
                <th scope="col" className="r">Latest</th>
                <th scope="col" className="r">Weight</th>
              </tr>
            </thead>
            <tbody>
              {caps?.streams.filter((s) => s.kind !== "truth").map((s) => {
                const st = s.stats;
                const polledReading = ["rssi", "quality", "rate"].includes(s.kind);
                const stale = polledReading && st && st.poll_hz && st.refresh_hz !== null && st.refresh_hz < 0.1 * st.poll_hz;
                return (
                  <tr key={s.name}>
                    <td>
                      {s.label}
                      {stale && <span className="caption"> (cached by the driver)</span>}
                    </td>
                    <td className="r num">{fmtNum(s.nominal_hz, 0)} Hz</td>
                    <td className="r num">{fmtNum(st?.poll_hz, 1)}</td>
                    <td className={stale ? "r num motion-text" : "r num"}>{fmtNum(st?.refresh_hz, 2)}</td>
                    <td className="r num">{st?.quant_step ? Number(st.quant_step.toPrecision(2)).toString() : "–"}</td>
                    <td className="r num">{st?.loss !== null && st?.loss !== undefined ? `${(st.loss * 100).toFixed(1)}%` : "–"}</td>
                    <td className="r num">
                      {fmtNum(st?.last, 2)} {s.unit}
                    </td>
                    <td className="r num">{fmtNum(caps.detector.weights[s.name], 2)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <div className="two-col">
        {src?.kind === "live" && <Anchors />}
        {caps && <DetectorSettings cfg={caps.detector.config} />}
        <ThemeCard />
      </div>
    </div>
  );
}

function Anchors() {
  const [rows, setRows] = useState<AnchorRow[] | null>(null);
  const [edits, setEdits] = useState<Record<string, Partial<AnchorRow>>>({});
  useEffect(() => {
    void api.get<AnchorRow[]>("/api/anchors").then(setRows).catch(() => setRows([]));
  }, []);
  const save = async () => {
    const out = await attempt(() => api.patch<AnchorRow[]>("/api/anchors", { changes: edits }), "Anchors saved; probing restarted");
    if (out) {
      setRows(out);
      setEdits({});
    }
  };
  return (
    <section className="sheet sheet-pad">
      <h3>Probe anchors</h3>
      <p className="caption">Devices on your network the laptop pings. Name them by room so the map and recordings read well. Mesh nodes work best; phones sleep and answer erratically.</p>
      {rows === null ? (
        <p className="caption">Looking for devices…</p>
      ) : (
        <ul className="anchor-list">
          {rows.map((a) => {
            const e = { ...a, ...edits[a.id] };
            return (
              <li key={a.id}>
                <label className="check">
                  <input type="checkbox" checked={e.enabled} onChange={(ev) => setEdits({ ...edits, [a.id]: { ...edits[a.id], enabled: ev.target.checked } })} />
                  <span className="sr-only">Probe {a.name}</span>
                </label>
                <input
                  className="input"
                  value={e.name}
                  aria-label={`Name for ${a.ip}`}
                  onChange={(ev) => setEdits({ ...edits, [a.id]: { ...edits[a.id], name: ev.target.value } })}
                />
                <span className="caption">
                  {KIND_TEXT[a.kind] ?? a.kind}, <span className="num">{a.ip}</span>
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <button className="btn btn-primary" disabled={!Object.keys(edits).length} onClick={save}>
        Save anchors
      </button>
    </section>
  );
}

function DetectorSettings({ cfg }: { cfg: DetectorConfig }) {
  const [draft, setDraft] = useState<Partial<DetectorConfig>>({});
  const fields: [keyof DetectorConfig, string, string][] = [
    ["z_on", "Motion threshold (z)", "Higher means fewer false alarms and more missed motion."],
    ["z_off", "Release threshold (z)", "Motion ends once the smoothed score falls below this."],
    ["hold_s", "Hold after motion (s)", "Keeps a brief pause from splitting one walk into two events."],
    ["presence_hold_s", "Time until quiet (s)", "How long the house must be still before it reads as quiet."],
    ["calib_s", "Baseline learning (s)", "How long a relearn listens for."],
  ];
  const save = () => attempt(() => api.patch("/api/detector", draft), "Detector settings saved").then(() => setDraft({}));
  return (
    <section className="sheet sheet-pad">
      <h3>Detector</h3>
      <div className="form-grid">
        {fields.map(([k, label, hint]) => (
          <label key={k} className="field">
            <span>{label}</span>
            <input
              className="input num"
              inputMode="decimal"
              value={String(draft[k] ?? cfg[k])}
              onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })}
              aria-describedby={`hint-${k}`}
            />
            <small id={`hint-${k}`} className="caption">{hint}</small>
          </label>
        ))}
      </div>
      <div className="sheet-tools">
        <button className="btn btn-primary" disabled={!Object.keys(draft).length} onClick={save}>
          Save settings
        </button>
        <button className="btn" onClick={() => attempt(() => api.post("/api/calibrate"), "Relearning the baseline")}>
          Relearn baseline
        </button>
      </div>
    </section>
  );
}

function ThemeCard() {
  const read = () => {
    try {
      return localStorage.getItem("theme") ?? "auto";
    } catch {
      return "auto";
    }
  };
  const [theme, setTheme] = useState(read);
  const apply = (t: string) => {
    setTheme(t);
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    try {
      localStorage.setItem("theme", t);
    } catch {
      /* storage unavailable: the choice lasts for this visit */
    }
  };
  return (
    <section className="sheet sheet-pad">
      <h3>Appearance</h3>
      <div className="segmented" role="group" aria-label="Theme">
        {[
          ["auto", "Match system"],
          ["light", "Day paper"],
          ["dark", "Night paper"],
        ].map(([id, label]) => (
          <button key={id} aria-pressed={theme === id} onClick={() => apply(id)}>
            {label}
          </button>
        ))}
      </div>
    </section>
  );
}
