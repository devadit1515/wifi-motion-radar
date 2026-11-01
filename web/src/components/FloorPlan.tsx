import { useRef } from "react";
import type { Layout, Truth } from "../lib/protocol";

interface Props {
  layout: Layout;
  truth: Truth | undefined;
  hotAnchors: Set<string>;
}

/** Simulator only: the floor plan with the simulated person. This is ground truth
 *  the detector never sees, shown so the demo can be checked against reality. */
export function FloorPlan({ layout, truth, hotAnchors }: Props) {
  const trail = useRef<[number, number][]>([]);
  if (truth?.x != null && truth.y != null) {
    trail.current.push([truth.x, truth.y]);
    if (trail.current.length > 40) trail.current.shift();
  } else {
    trail.current = [];
  }
  const { width: W, height: H } = layout;
  const pad = 0.4;
  const [rx, ry] = layout.router;
  const [lx, ly] = layout.laptop;
  const walking = truth?.state === "walking";

  return (
    <svg
      className="floorplan"
      viewBox={`${-pad} ${-pad} ${W + 2 * pad} ${H + 2 * pad}`}
      role="img"
      aria-label={`Simulated home. The simulated person is ${truth?.state ?? "unknown"}.`}
    >
      {layout.rooms.map((r) => (
        <text key={r.name} x={r.x + 0.3} y={r.y + 0.6} className="fp-room">
          {r.name}
        </text>
      ))}
      {Object.entries(layout.nodes).map(([id, [nx, ny]]) => (
        <line key={id} x1={rx} y1={ry} x2={nx} y2={ny} className={hotAnchors.has(id) ? "fp-link hot" : "fp-link"} />
      ))}
      <line x1={lx} y1={ly} x2={rx} y2={ry} className={hotAnchors.has("gw") ? "fp-link hot" : "fp-link"} />
      {layout.walls.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} className="fp-wall" />
      ))}
      <rect x={rx - 0.2} y={ry - 0.2} width={0.4} height={0.4} className="fp-device" />
      <text x={rx + 0.35} y={ry + 0.12} className="fp-label">Router</text>
      <rect x={lx - 0.28} y={ly - 0.18} width={0.56} height={0.36} rx={0.06} className="fp-laptop" />
      <text x={lx + 0.4} y={ly + 0.12} className="fp-label">Laptop</text>
      {Object.entries(layout.nodes).map(([id, [nx, ny]]) => (
        <g key={id}>
          <circle cx={nx} cy={ny} r={0.18} className="fp-device" />
          <text x={nx + 0.3} y={ny + 0.12} className="fp-label">
            Node .{id.slice(1)}
          </text>
        </g>
      ))}
      {trail.current.length > 1 && (
        <polyline points={trail.current.map(([x, y]) => `${x},${y}`).join(" ")} className="fp-trail" />
      )}
      {truth?.x != null && truth.y != null && (
        <circle cx={truth.x} cy={truth.y} r={0.24} className={walking ? "fp-person moving" : "fp-person"} />
      )}
    </svg>
  );
}
