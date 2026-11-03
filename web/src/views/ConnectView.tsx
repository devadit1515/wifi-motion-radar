import { DOWNLOAD, ENGINE_PORT, REPO } from "../lib/engine";
import { useLive } from "../lib/store";

/** Shown by the hosted website until the helper on this computer answers. */
export function ConnectView() {
  const live = useLive();
  const lost = live.everOpen;

  return (
    <section className="page connect" aria-labelledby="connect-title">
      <header className="page-head">
        <h2 id="connect-title">{lost ? "The helper stopped" : "Start the helper on this computer"}</h2>
        <p>
          Motion Radar senses with this computer's own WiFi card and microphone. A website cannot reach those, so a
          small helper app does the sensing and this page shows what it sees. Nothing leaves your computer.
        </p>
      </header>

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
            If the browser asks whether this site may connect to apps and devices on this computer, choose <b>Allow</b>.
            The helper answers this website and nothing else.
          </p>
        </li>
      </ol>

      <p className="connect-status" role="status">
        <span className="conn conn-closed" aria-hidden="true" />
        {lost ? "Reconnecting to the helper" : "Looking for the helper"} on port <span className="num">{ENGINE_PORT}</span>. This page
        connects by itself once it is running.
      </p>

      <p className="caption muted">
        Works in Chrome and Edge. Without internet, the helper also serves this dashboard at{" "}
        <a href={`http://127.0.0.1:${ENGINE_PORT}`}>127.0.0.1:{ENGINE_PORT}</a>. <a href={REPO}>Source code</a>.
      </p>
    </section>
  );
}
