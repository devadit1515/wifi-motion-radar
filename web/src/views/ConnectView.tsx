import { useEffect, useState } from "react";
import { DOWNLOAD, ENGINE_PORT, REPO } from "../lib/engine";
import { useLive } from "../lib/store";

const LOCAL = `http://127.0.0.1:${ENGINE_PORT}`;

/** Chrome and Edge ask before a website may reach apps on this computer. The permission
 *  has had several names while the feature rolled out; any of them reporting "denied"
 *  means the browser, not the helper, is in the way. */
function useLocalAccess(): PermissionState | null {
  const [state, setState] = useState<PermissionState | null>(null);
  useEffect(() => {
    let alive = true;
    const statuses: PermissionStatus[] = [];
    const update = () => {
      if (!alive) return;
      const states = statuses.map((s) => s.state);
      setState(states.includes("denied") ? "denied" : states.includes("prompt") ? "prompt" : states[0] ?? null);
    };
    for (const name of ["loopback-network", "local-network-access", "local-network"]) {
      navigator.permissions
        ?.query({ name } as unknown as PermissionDescriptor)
        .then((s) => {
          statuses.push(s);
          s.onchange = update;
          update();
        })
        .catch(() => undefined); // this browser does not know that name
    }
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

/** Shown by the hosted website until the helper on this computer answers. */
export function ConnectView() {
  const live = useLive();
  const access = useLocalAccess();
  const lost = live.everOpen;
  const blocked = access === "denied";

  return (
    <section className="page connect" aria-labelledby="connect-title">
      <header className="page-head">
        <h2 id="connect-title">
          {blocked ? "Your browser is blocking the helper" : lost ? "The helper stopped" : "Start the helper on this computer"}
        </h2>
        <p>
          Motion Radar senses with this computer's own WiFi card and microphone. A website cannot reach those, so a
          small helper app does the sensing and this page shows what it sees. Nothing leaves your computer.
        </p>
      </header>

      {blocked && (
        <div className="sheet sheet-pad connect-blocked" role="alert">
          <h3>Allow this site to reach apps on this computer</h3>
          <p className="caption">
            Click the icon at the left of the address bar, open <b>Site settings</b>, set <b>Local network access</b> (or{" "}
            <b>Apps on this device</b>) to <b>Allow</b>, then reload. Or skip the browser setting and use the copy of this
            dashboard the helper serves itself:
          </p>
          <a className="btn btn-primary" href={LOCAL}>
            Open the dashboard from the helper
          </a>
        </div>
      )}

      <ol className="sheet sheet-pad connect-steps">
        <li>
          <h3>Download the helper</h3>
          <p className="caption">Windows 10 or 11. About 100 MB, no installation.</p>
          <a className="btn btn-primary" href={DOWNLOAD}>
            Download for Windows
          </a>
        </li>
        <li>
          <h3>Unzip it and open MotionRadar.exe</h3>
          <p className="caption">
            Windows may say it protected your PC from an unrecognised app. Choose <b>More info</b>, then <b>Run anyway</b>.
            Keep its window open while you use this page; closing it stops sensing and frees the microphone.
          </p>
        </li>
        <li>
          <h3>Allow the connection</h3>
          <p className="caption">
            The browser asks whether this site may connect to apps on this computer. Choose <b>Allow</b>; the helper
            answers this website and nothing else.
          </p>
        </li>
      </ol>

      <p className="connect-status" role="status">
        <span className="conn conn-closed" aria-hidden="true" />
        <span>
          {lost ? "Reconnecting to the helper" : "Looking for the helper"} on port <span className="num">{ENGINE_PORT}</span>;
          this page connects by itself once it is running.
        </span>
      </p>

      <p className="caption muted">
        Helper running but this page will not connect? <a href={LOCAL}>Open the dashboard from the helper</a> at{" "}
        {LOCAL.replace("http://", "")}; it works without internet too. Chrome and Edge on Windows.{" "}
        <a href={REPO}>Source code</a>.
      </p>
    </section>
  );
}
