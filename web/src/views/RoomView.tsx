import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { api } from "../lib/api";
import { palette, prefersReducedMotion, setFont, useCanvasLoop } from "../lib/canvas";
import { fmtNum } from "../lib/format";
import type { Room, SonarExtras } from "../lib/protocol";
import { store, useLive } from "../lib/store";
import { attempt } from "../lib/toast";

const DEFAULT_ROOM: Room = { width: 3.5, depth: 4.0, pc_x: 1.75, mic_spacing: null, pd_center: null, flip: false };
const DEFAULT_SPACING = 0.15;

interface Placed {
  x: number; // metres from the left wall
  y: number; // metres out from the PC's wall
  r: number;
  angle: number | null; // degrees, 0 = straight ahead, negative = left
}

/** Where the sonar's target sits in the room. Distance is measured; direction comes
 *  from the path difference between the two microphones, centred by calibration. */
function place(room: Room, s: SonarExtras | undefined): Placed | null {
  const t = s?.target;
  if (!t) return null;
  if (t.path_diff === null || t.path_diff === undefined) {
    return { x: room.pc_x, y: t.r, r: t.r, angle: null };
  }
  const center = room.pd_center ?? s?.direct_diff_m ?? 0;
  const spacing = room.mic_spacing ?? DEFAULT_SPACING;
  const sin = Math.max(-1, Math.min(1, (t.path_diff - center) / spacing)) * (room.flip ? -1 : 1);
  const a = Math.asin(sin);
  return { x: room.pc_x + t.r * Math.sin(a), y: t.r * Math.cos(a), r: t.r, angle: (a * 180) / Math.PI };
}

export function RoomView() {
  const live = useLive();
  const enabled = live.hello?.sonar_enabled ?? false;
  const sonar = live.last?.sonar;
  const [room, setRoom] = useState<Room>(DEFAULT_ROOM);
  const [saved, setSaved] = useState(true);
  const roomRef = useRef(room);
  roomRef.current = room;
  const recentPd = useRef<{ t: number; v: number }[]>([]);

  useEffect(() => {
    void api.get<Room>("/api/room").then(setRoom).catch(() => undefined);
  }, []);

  // Keep the last few seconds of path differences for centre calibration.
  const pd = sonar?.target?.path_diff;
  if (pd !== null && pd !== undefined && sonar?.target?.moving) {
    const now = performance.now();
    recentPd.current.push({ t: now, v: pd });
    recentPd.current = recentPd.current.filter((p) => now - p.t < 3000);
  }

  const edit = (patch: Partial<Room>) => {
    setRoom({ ...room, ...patch });
    setSaved(false);
  };
  const save = async (r: Room = room) => {
    const out = await attempt(() => api.put<Room>("/api/room", r), "Room saved");
    if (out) {
      setRoom(out);
      setSaved(true);
    }
  };
  const calibrate = () => {
    const vals = recentPd.current.map((p) => p.v).sort((a, b) => a - b);
    if (vals.length < 5) return;
    void save({ ...room, pd_center: vals[Math.floor(vals.length / 2)] });
  };
  const toggle = () =>
    attempt(() => api.post("/api/sonar", { enabled: !enabled }), enabled ? "Sonar off" : "Sonar on: learning the room");

  const placed = place(room, sonar);
  const h = sonar?.health;
  const listening = enabled && h?.status === "ok";

  return (
    <div className="page">
      <header className="page-head">
        <h2>Room</h2>
        <p>
          A sonar built from this PC's own speakers and microphones. It plays a quiet sweep at 18.5 to 22 kHz,
          above most adults' hearing, listens for the echoes, and marks whatever moves.
        </p>
      </header>

      <div className="room-layout">
        <section className="sheet room-canvas-sheet" aria-label="Room plan">
          <RoomCanvas roomRef={roomRef} enabled={enabled} />
        </section>

        <aside className="room-side">
          <section className="sheet sheet-pad">
            <div className="sonar-status">
              <span className={listening ? "status-dot on" : "status-dot"} aria-hidden="true" />
              <h3>{!enabled ? "Sonar is off" : listening ? "Listening" : h?.status === "locking" ? "Finding the direct path" : "Starting"}</h3>
            </div>
            {enabled && h?.message && h.status !== "locking" && <p className="motion-text">{h.message}</p>}
            {enabled && sonar?.error && <p className="motion-text">{sonar.error}</p>}
            <button className={enabled ? "btn" : "btn btn-primary"} onClick={toggle}>
              {enabled ? "Stop sonar" : "Start sonar"}
            </button>
            {!enabled && (
              <p className="caption">
                Starting the sonar switches to live mode and takes the microphone exclusively, so calls and recording
                apps cannot use it while it runs. Speaker volume around 70% works best.
              </p>
            )}
            {enabled && (
              <dl className="facts">
                <dt>Seen</dt>
                <dd>
                  {!sonar?.target
                    ? "nobody"
                    : sonar.target.still
                      ? "someone still (sway and breathing)"
                      : sonar.target.moving
                        ? "someone moving"
                        : "just stopped"}
                </dd>
                <dt>Distance</dt>
                <dd className="num">{placed ? `${placed.r.toFixed(2)} m` : "–"}</dd>
                <dt>Direction</dt>
                <dd>{placed?.angle == null ? "–" : directionText(placed.angle)}</dd>
                <dt>Echo strength</dt>
                <dd className="num">{h?.snr_db == null ? "–" : `${fmtNum(h.snr_db, 0)} dB`}</dd>
                <dt>Updates</dt>
                <dd className="num">{sonar?.fps ? `${sonar.fps} per second` : "–"}</dd>
              </dl>
            )}
          </section>

          <section className="sheet sheet-pad">
            <h3>Your room</h3>
            <p className="caption">Sound can measure distance, not the shape of a room, so draw it here once.</p>
            <div className="form-grid room-form">
              <label className="field">
                <span>Width (m)</span>
                <input className="input num" inputMode="decimal" value={room.width} onChange={(e) => edit({ width: Number(e.target.value) || 0 })} />
              </label>
              <label className="field">
                <span>Depth from the PC's wall (m)</span>
                <input className="input num" inputMode="decimal" value={room.depth} onChange={(e) => edit({ depth: Number(e.target.value) || 0 })} />
              </label>
              <label className="field wide">
                <span>PC position from the left wall: {room.pc_x.toFixed(2)} m</span>
                <input type="range" min={0} max={room.width} step={0.05} value={room.pc_x} onChange={(e) => edit({ pc_x: Number(e.target.value) })} />
              </label>
            </div>
            {sonar?.echoes && sonar.echoes.length > 0 && (
              <p className="caption">
                Fixed echoes at {sonar.echoes.map((e) => `${e.toFixed(2)} m`).join(", ")}. The farthest strong one is often
                a wall.
              </p>
            )}
            <button className="btn btn-primary" disabled={saved} onClick={() => void save()}>
              Save room
            </button>
          </section>

          <section className="sheet sheet-pad">
            <h3>Direction</h3>
            <p className="caption">
              Direction comes from the tiny delay between the two microphones. Stand about 1.5 m straight in front of the
              screen, sway a little so the sonar sees you, then press the button.
            </p>
            <button className="btn" disabled={recentPd.current.length < 5} onClick={calibrate}>
              I'm straight in front of the screen
            </button>
            <label className="field">
              <span>Direction sensitivity (microphone spacing): {(room.mic_spacing ?? DEFAULT_SPACING).toFixed(2)} m</span>
              <input type="range" min={0.04} max={0.5} step={0.01} value={room.mic_spacing ?? DEFAULT_SPACING} onChange={(e) => edit({ mic_spacing: Number(e.target.value) })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={room.flip} onChange={(e) => edit({ flip: e.target.checked })} />
              Swap left and right
            </label>
          </section>
        </aside>
      </div>
      <p className="caption room-note">
        Red pulsing dot: someone walking. Hollow dot: someone present but still, found by the tiny sway and breathing
        movements a standing or sitting person makes. Rough by design: distance to about 20 to 40 cm, direction to about
        20 degrees. Someone who stays put for more than a minute or so is absorbed into the background. Furniture
        between you and the screen blocks the sound.
      </p>
    </div>
  );
}

function directionText(deg: number): string {
  const a = Math.round(Math.abs(deg));
  if (a < 8) return "straight ahead";
  return `${a}° to the ${deg < 0 ? "left" : "right"}`;
}

function RoomCanvas({ roomRef, enabled }: { roomRef: MutableRefObject<Room>; enabled: boolean }) {
  const view = useRef({ x: NaN, y: NaN, trail: [] as { x: number; y: number; t: number }[], lastSeq: -1 });
  const reduce = prefersReducedMotion();

  const ref = useCanvasLoop((ctx, w, h, ts) => {
    const p = palette();
    const room = roomRef.current;
    const s = store.last?.sonar;
    const m = 44;
    const scale = Math.max(10, Math.min((w - 2 * m) / room.width, (h - 2 * m) / room.depth));
    const rw = room.width * scale;
    const rd = room.depth * scale;
    const ox = (w - rw) / 2;
    const oy = (h - rd) / 2;
    const px = ox + room.pc_x * scale;
    const py = oy + rd;
    const X = (x: number) => ox + x * scale;
    const Y = (y: number) => py - y * scale;

    // Floor and half-metre grid.
    ctx.fillStyle = p.raised;
    ctx.fillRect(ox, oy, rw, rd);
    ctx.save();
    ctx.beginPath();
    ctx.rect(ox, oy, rw, rd);
    ctx.clip();
    ctx.strokeStyle = p.grid;
    ctx.lineWidth = 1;
    for (let gx = 0.5; gx < room.width; gx += 0.5) {
      ctx.beginPath();
      ctx.moveTo(Math.round(X(gx)) + 0.5, oy);
      ctx.lineTo(Math.round(X(gx)) + 0.5, oy + rd);
      ctx.stroke();
    }
    for (let gy = 0.5; gy < room.depth; gy += 0.5) {
      ctx.beginPath();
      ctx.moveTo(ox, Math.round(Y(gy)) + 0.5);
      ctx.lineTo(ox + rw, Math.round(Y(gy)) + 0.5);
      ctx.stroke();
    }

    // What the sonar hears move, by distance: red bands at those ranges. Intensity
    // 0.35 is a residual about 3x its usual level; below that it is ordinary noise.
    if (enabled && s?.bins) {
      s.bins.forEach((v, i) => {
        if (v < 0.35) return;
        const r = s.r_min + (i + 0.5) * s.bin_m;
        ctx.strokeStyle = p.red;
        ctx.globalAlpha = Math.min(0.5, ((v - 0.35) / 0.65) * 0.6);
        ctx.lineWidth = Math.max(1, s.bin_m * scale);
        ctx.beginPath();
        ctx.arc(px, py, r * scale, Math.PI, 2 * Math.PI);
        ctx.stroke();
      });
      ctx.globalAlpha = 1;
    }

    // Range rings every metre.
    ctx.setLineDash([3, 5]);
    ctx.strokeStyle = p.gridMajor;
    ctx.lineWidth = 1;
    setFont(ctx, 11, 480, "condensed");
    ctx.fillStyle = p.inkSoft;
    for (let r = 1; r <= 6; r++) {
      ctx.beginPath();
      ctx.arc(px, py, r * scale, Math.PI, 2 * Math.PI);
      ctx.stroke();
      ctx.fillText(`${r} m`, px + 4, py - r * scale + 12);
    }
    ctx.setLineDash([]);

    // The target: a dot that pulses while it moves, with a short fading trail.
    const v = view.current;
    const placed = enabled ? place(room, s) : null;
    const now = performance.now();
    if (placed) {
      const tx = Math.min(Math.max(placed.x, 0), room.width);
      const ty = Math.min(Math.max(placed.y, 0), room.depth);
      v.x = Number.isNaN(v.x) ? tx : v.x + (tx - v.x) * 0.25;
      v.y = Number.isNaN(v.y) ? ty : v.y + (ty - v.y) * 0.25;
      const seq = store.last?.seq ?? 0;
      if (s?.target?.moving && seq !== v.lastSeq) {
        v.trail.push({ x: v.x, y: v.y, t: now });
        v.lastSeq = seq;
      }
    } else {
      v.x = v.y = NaN;
    }
    v.trail = v.trail.filter((pt) => now - pt.t < 4000);
    if (v.trail.length > 1) {
      ctx.strokeStyle = p.red;
      ctx.lineWidth = 2;
      for (let i = 1; i < v.trail.length; i++) {
        ctx.globalAlpha = 0.5 * (1 - (now - v.trail[i].t) / 4000);
        ctx.beginPath();
        ctx.moveTo(X(v.trail[i - 1].x), Y(v.trail[i - 1].y));
        ctx.lineTo(X(v.trail[i].x), Y(v.trail[i].y));
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    if (placed && !Number.isNaN(v.x)) {
      const cx = X(v.x);
      const cy = Y(v.y);
      const moving = !!s?.target?.moving;
      if (placed.angle === null) {
        // One microphone: distance only, so show the whole arc it could be on.
        ctx.strokeStyle = p.red;
        ctx.lineWidth = 6;
        ctx.globalAlpha = 0.6;
        ctx.beginPath();
        ctx.arc(px, py, placed.r * scale, Math.PI, 2 * Math.PI);
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      if (moving && !reduce) {
        const phase = (ts % 900) / 900;
        ctx.strokeStyle = p.red;
        ctx.globalAlpha = 1 - phase;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(cx, cy, 10 + 22 * phase, 0, Math.PI * 2);
        ctx.stroke();
        ctx.globalAlpha = 1;
      } else if (s?.target?.still) {
        // Present but barely moving: a soft, slow halo instead of the pulse.
        const breathe = reduce ? 0.5 : 0.5 + 0.5 * Math.sin((ts / 2600) * Math.PI * 2);
        ctx.fillStyle = p.inkSoft;
        ctx.globalAlpha = 0.12 + 0.1 * breathe;
        ctx.beginPath();
        ctx.arc(cx, cy, 22, 0, Math.PI * 2);
        ctx.fill();
        ctx.globalAlpha = 1;
      }
      ctx.fillStyle = moving ? p.red : p.raised;
      ctx.strokeStyle = moving ? p.red : p.ink;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(cx, cy, 9, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();

    // Walls, the PC and dimensions.
    ctx.strokeStyle = p.ink;
    ctx.lineWidth = 3;
    ctx.strokeRect(ox, oy, rw, rd);
    ctx.fillStyle = p.ink;
    ctx.fillRect(px - 18, py - 6, 36, 8);
    setFont(ctx, 12, 600);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText("This PC", px, py + 8);
    setFont(ctx, 12, 520, "condensed");
    ctx.fillStyle = p.inkSoft;
    ctx.textBaseline = "bottom";
    ctx.fillText(`${room.width.toFixed(1)} m`, ox + rw / 2, oy - 8);
    ctx.save();
    ctx.translate(ox + rw + 10, oy + rd / 2);
    ctx.rotate(Math.PI / 2);
    ctx.textBaseline = "bottom";
    ctx.fillText(`${room.depth.toFixed(1)} m`, 0, 0);
    ctx.restore();

    if (!enabled) {
      setFont(ctx, 16, 600);
      ctx.fillStyle = p.inkSoft;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Sonar is off", ox + rw / 2, oy + rd / 2);
    }
  });

  return (
    <canvas
      ref={ref}
      className="room-canvas"
      role="img"
      aria-label="Room plan: the PC on the bottom wall, range rings every metre, and a dot where the sonar hears motion."
    />
  );
}
