"""Physics-informed home simulator.

A person is a moving point scatterer inside a 2-D floor plan. Each radio segment
(laptop to router, router to each mesh node) carries a complex channel: the
line-of-sight ray, fixed scatterers standing in for walls and furniture, and the
person's scattered ray, with body shadowing when the person stands inside the
segment's first Fresnel zone. Motion changes the person's path length, the rays add
up differently, and the received power fades.

The fading is then pushed through crude but mechanistic models of what a laptop can
actually observe:

  * RTT: rate adaptation tracks a smoothed SNR, so a fade deeper than its margin causes
    retransmissions, and each retry adds airtime and backoff to the round trip.
  * Driver retry counter: the same retries, as a log rate per 50 ms poll.
  * RSSI: averaged and cached by the "driver", refreshed slowly, quantized to 1 dB.

It also injects a confound real homes have: bursts of other household traffic that
inflate RTT with no one moving. The simulator exists for tests with ground truth and
a demo mode that runs anywhere. It is never presented as a measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from radar.sources.base import Emit, Sample, Source, StreamInfo, now

FREQ_HZ = 5.24e9
LAMBDA = 299_792_458.0 / FREQ_HZ  # 5.7 cm
K = 2 * math.pi / LAMBDA
NOISE_FLOOR_DBM = -92.0
PERSON_GAMMA = 0.55
SHADOW_DB = 9.0
BODY_RADIUS = 0.2

STATES = ("empty", "still", "walking")
STATE_CODE = {s: i for i, s in enumerate(STATES)}

# 802.11ac 80 MHz 2x2: minimum SNR (dB) for each PHY rate (Mbit/s).
MCS_TABLE = [(5, 58.5), (8, 117.0), (11, 175.5), (14, 234.0), (17, 351.0), (21, 468.0),
             (23, 526.5), (25, 585.0), (29, 702.0), (32, 780.0), (34, 866.7)]  # fmt: skip


@dataclass
class Layout:
    width: float = 12.0
    height: float = 8.0
    router: tuple[float, float] = (1.6, 6.6)
    laptop: tuple[float, float] = (9.4, 1.8)
    nodes: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {"h244": (10.6, 6.9), "h245": (3.2, 1.0), "h246": (6.9, 5.4)}
    )
    entrance: tuple[float, float] = (0.4, 2.4)
    walls: list[tuple[float, float, float, float]] = field(
        default_factory=lambda: [
            (0, 0, 12, 0), (12, 0, 12, 8), (12, 8, 0, 8), (0, 8, 0, 3.2), (0, 1.6, 0, 0),
            (6, 0, 6, 3.0), (6, 4.4, 6, 8), (6, 4, 8.2, 4), (9.4, 4, 12, 4),
        ]
    )  # fmt: skip
    rooms: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "Living room", "x": 0, "y": 0, "w": 6, "h": 8},
            {"name": "Bedroom", "x": 6, "y": 0, "w": 6, "h": 4},
            {"name": "Kitchen", "x": 6, "y": 4, "w": 6, "h": 4},
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width, "height": self.height, "router": self.router,
            "laptop": self.laptop, "nodes": self.nodes, "entrance": self.entrance,
            "walls": self.walls, "rooms": self.rooms,
        }  # fmt: skip


def _leg_shadow_db(x: np.ndarray, p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Body-shadowing loss (dB) on each straight leg p[i]→q[i] for a person at x.

    Loss is largest when the person stands on the leg and falls off over the first
    Fresnel zone radius plus the body radius; legs the person is not beside lose nothing.
    """
    v = q - p
    length = np.linalg.norm(v, axis=1)
    rel = x - p
    u = np.einsum("ij,ij->i", rel, v) / length**2
    perp = np.abs(v[:, 0] * rel[:, 1] - v[:, 1] * rel[:, 0]) / length
    inside = (u > 0) & (u < 1)
    fresnel = np.sqrt(LAMBDA * np.clip(u * (1 - u), 0, None) * length)
    return np.where(inside, SHADOW_DB * np.exp(-((perp / (fresnel + BODY_RADIUS)) ** 2)), 0.0)


class Segment:
    """One radio hop: line of sight plus one bounce off each fixed scatterer."""

    def __init__(self, a, b, scat_xy, scat_gamma, target_dbm: float) -> None:
        self.a = np.asarray(a, float)
        self.b = np.asarray(b, float)
        self.d = float(np.linalg.norm(self.b - self.a))
        self.los = (1.0 / self.d) * np.exp(-1j * K * self.d)
        d1 = np.linalg.norm(scat_xy - self.a, axis=1)
        d2 = np.linalg.norm(scat_xy - self.b, axis=1)
        self.paths = scat_gamma / (d1 * d2) * np.exp(-1j * K * (d1 + d2))
        self.static = self.los + np.sum(self.paths)
        n = len(scat_xy)
        # Legs: [a→b] then [a→s_i] then [s_i→b]; shadowing is evaluated on all at once.
        self._p = np.vstack([self.a, np.repeat(self.a[None], n, 0), scat_xy])
        self._q = np.vstack([self.b, scat_xy, np.repeat(self.b[None], n, 0)])
        self._n = n
        self.p0 = target_dbm - 20 * math.log10(abs(self.static))
        self.snr_ref = target_dbm - NOISE_FLOOR_DBM

    def channel(self, x: np.ndarray | None) -> complex:
        if x is None:
            return complex(self.static)
        d1 = max(float(np.linalg.norm(x - self.a)), 0.3)
        d2 = max(float(np.linalg.norm(x - self.b)), 0.3)
        bounce = PERSON_GAMMA / (d1 * d2) * np.exp(-1j * K * (d1 + d2))
        att = _leg_shadow_db(x, self._p, self._q)
        n = self._n
        path_att = np.maximum(att[1 : n + 1], att[n + 1 :])
        h = self.los * 10 ** (-att[0] / 20) + np.sum(self.paths * 10 ** (-path_att / 20))
        return complex(h + bounce)

    def power(self, h: complex) -> float:
        return self.p0 + 20 * math.log10(max(abs(h), 1e-9))


class HomeSim:
    """Simulation state, advanced in fixed steps by a clock the caller owns."""

    def __init__(self, seed: int = 7, rssi_refresh_s: float = 1.0, load: bool = True) -> None:
        self.rng = np.random.default_rng(seed)
        self.layout = Layout()
        self.rssi_refresh_s = rssi_refresh_s
        L = self.layout
        n_scat = 14
        scat = np.column_stack(
            [self.rng.uniform(0, L.width, n_scat), self.rng.uniform(0, L.height, n_scat)]
        )
        gamma = self.rng.uniform(0.15, 0.5, n_scat)
        self.link = Segment(L.laptop, L.router, scat, gamma, target_dbm=-58.0)
        self.backhaul = {
            nid: Segment(L.router, xy, scat, gamma, target_dbm=self.rng.uniform(-64, -55))
            for nid, xy in L.nodes.items()
        }
        self.base_rtt = {"gw": 1.9} | {
            nid: float(self.rng.uniform(3.0, 4.4)) for nid in L.nodes
        }
        self.anchor_ids = list(self.base_rtt)
        self.snr_ewma: dict[str, float] = {"link": self.link.snr_ref} | {
            nid: s.snr_ref for nid, s in self.backhaul.items()
        }

        self.t = 0.0
        self.mode = "auto"
        self.state = "empty"
        self.state_until = self.rng.uniform(15, 30)
        self.pos: np.ndarray | None = None
        self.waypoint: np.ndarray | None = None
        self.leaving = False
        self.speed = 1.0
        self.anchor_pos = np.zeros(2)
        self.breath_dir = np.array([1.0, 0.0])
        self.fidget_until = -1.0
        self.fidget_dir = np.zeros(2)

        # Household traffic bursts (someone else streaming): RTT rises, nobody moves.
        self.load_on = False
        self.load_until = self.rng.exponential(40.0) if load else math.inf

        self._power_acc: list[float] = []
        self._rssi = round(self.link.power(self.link.channel(None)))
        self._rssi_next = 0.0
        self._counters = {"tx": 0, "retry": 0, "failed": 0}
        self._h_cache: dict[str, complex] = {}

    # ---- person ----------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode not in ("auto", *STATES):
            raise ValueError(mode)
        self.mode = mode
        if mode != "auto" and mode != self.state:
            self._enter(mode)

    def _random_point(self) -> np.ndarray:
        L = self.layout
        return np.array(
            [self.rng.uniform(0.6, L.width - 0.6), self.rng.uniform(0.6, L.height - 0.6)]
        )

    def _enter(self, state: str) -> None:
        if state == "empty":
            self.pos, self.waypoint, self.leaving = None, None, False
        elif state == "walking":
            if self.pos is None:
                self.pos = np.array(self.layout.entrance, float)
            self.waypoint = self._random_point()
            self.speed = float(self.rng.uniform(0.8, 1.3))
            self.leaving = False
        elif state == "still":
            if self.pos is None:
                self.pos = self._random_point()
            self.anchor_pos = self.pos.copy()
            ang = self.rng.uniform(0, 2 * math.pi)
            self.breath_dir = np.array([math.cos(ang), math.sin(ang)])
        self.state = state
        self.state_until = self.t + {"empty": self.rng.uniform(25, 60),
                                     "walking": self.rng.uniform(15, 40),
                                     "still": self.rng.uniform(15, 40)}[state]  # fmt: skip

    def _schedule(self) -> None:
        if self.mode != "auto" or self.t < self.state_until or self.leaving:
            return
        if self.state == "empty" or self.state == "still":
            self._enter("walking")
        elif self.rng.random() < 0.55:
            self._enter("still")
        else:
            self.leaving = True
            self.waypoint = np.array(self.layout.entrance, float)

    def _move(self, dt: float) -> None:
        if self.state == "walking" and self.pos is not None and self.waypoint is not None:
            delta = self.waypoint - self.pos
            dist = float(np.linalg.norm(delta))
            step = self.speed * dt
            if dist <= step:
                self.pos = self.waypoint.copy()
                if self.leaving:
                    self._enter("empty")
                    return
                self.waypoint = self._random_point()
            else:
                self.pos = self.pos + delta / dist * step
        elif self.state == "still":
            breath = 0.004 * math.sin(2 * math.pi * 0.25 * self.t)
            if self.t > self.fidget_until and self.rng.random() < dt / 9.0:
                self.fidget_until = self.t + 0.5
                ang = self.rng.uniform(0, 2 * math.pi)
                self.fidget_dir = 0.03 * np.array([math.cos(ang), math.sin(ang)])
            left = max(0.0, self.fidget_until - self.t)
            fidget = self.fidget_dir * math.sin(math.pi * left / 0.5)
            self.pos = self.anchor_pos + breath * self.breath_dir + fidget

    def _body(self) -> np.ndarray | None:
        """Scatterer position, with the lateral sway of a walking gait (~1.9 Hz)."""
        if self.pos is None:
            return None
        if self.state != "walking" or self.waypoint is None:
            return self.pos
        d = self.waypoint - self.pos
        n = float(np.linalg.norm(d))
        if n < 1e-6:
            return self.pos
        lateral = np.array([-d[1], d[0]]) / n
        return self.pos + 0.04 * math.sin(2 * math.pi * 1.9 * self.t) * lateral

    # ---- radio -----------------------------------------------------------------
    def _per(self, key: str, power: float) -> float:
        """Frame error probability: rate adaptation targets the smoothed SNR minus a
        3 dB margin, so only fades deeper than that margin cause retries."""
        snr = power - NOISE_FLOOR_DBM + self.rng.normal(0, 1.0)
        ref = self.snr_ewma[key]
        return 0.03 + 0.97 / (1 + math.exp(-((ref - 3.0) - snr) / 1.2))

    def _attempts(self, per: float, limit: int = 7) -> int:
        """Transmission attempts until success; limit+1 means the frame was dropped."""
        n = 1
        while n <= limit and self.rng.random() < per:
            n += 1
        return n

    def advance(self, dt: float) -> None:
        self.t += dt
        self._schedule()
        self._move(dt)
        if self.t > self.load_until:
            self.load_on = not self.load_on
            self.load_until = self.t + (
                self.rng.exponential(2.5) if self.load_on else self.rng.exponential(45.0)
            )
        body = self._body()
        alpha = min(1.0, dt / 0.2)
        self._h_cache = {"link": self.link.channel(body)}
        for nid, seg in self.backhaul.items():
            self._h_cache[nid] = seg.channel(body)
        for key, h in self._h_cache.items():
            seg = self.link if key == "link" else self.backhaul[key]
            snr = seg.power(h) - NOISE_FLOOR_DBM
            self.snr_ewma[key] += alpha * (snr - self.snr_ewma[key])
        self._power_acc.append(self.link.power(self._h_cache["link"]))

    def ping(self, anchor: str) -> float:
        """One probe's round trip in ms, or NaN if dropped. The request and the reply
        each cross every hop, and each crossing can need retransmissions."""
        hops = ["link"] if anchor == "gw" else ["link", anchor]
        rtt = self.base_rtt[anchor] + self.rng.gamma(2.0, 0.12)
        if self.load_on:
            rtt += self.rng.gamma(2.0, 0.7)
        for direction in ("request", "reply"):
            for hop in hops:
                seg = self.link if hop == "link" else self.backhaul[hop]
                n = self._attempts(self._per(hop, seg.power(self._h_cache[hop])))
                ours = direction == "request" and hop == "link"  # what our driver counts
                if ours:
                    self._counters["tx"] += 1
                    self._counters["retry"] += min(n, 8) - 1
                if n > 7:
                    self._counters["failed"] += int(ours)
                    return math.nan
                rtt += (n - 1) * (0.12 + self.rng.uniform(0.05, 0.35))
        return rtt

    def background_tx(self, dt: float) -> None:
        """The laptop's own background chatter also rides the link and sees retries."""
        n = int(self.rng.poisson(30 * dt))
        per = self._per("link", self.link.power(self._h_cache["link"]))
        for _ in range(n):
            a = self._attempts(per)
            self._counters["tx"] += 1
            self._counters["retry"] += min(a, 8) - 1
            self._counters["failed"] += int(a > 7)

    def take_counters(self) -> dict[str, int]:
        c, self._counters = self._counters, {"tx": 0, "retry": 0, "failed": 0}
        return c

    def rssi(self) -> int:
        """Driver-style RSSI: averaged over the refresh interval, cached, 1 dB steps."""
        if self.t >= self._rssi_next and self._power_acc:
            avg = 10 * math.log10(np.mean(10 ** (np.asarray(self._power_acc) / 10)))
            self._rssi = int(round(avg + self.rng.normal(0, 0.6)))
            self._power_acc.clear()
            self._rssi_next = self.t + self.rssi_refresh_s
        return self._rssi

    def phy_rate(self) -> float:
        rate = MCS_TABLE[0][1]
        for snr_min, r in MCS_TABLE:
            if self.snr_ewma["link"] - 3.0 >= snr_min:
                rate = r
        return rate

    def truth(self) -> dict[str, Any]:
        x, y = (float(self.pos[0]), float(self.pos[1])) if self.pos is not None else (None, None)
        return {"state": self.state, "x": x, "y": y, "load": self.load_on, "mode": self.mode}


def sim_streams(anchor_ids: list[str], hz: float) -> list[StreamInfo]:
    names = {"gw": "Router"} | {a: f"Mesh node .{a[1:]}" for a in anchor_ids if a != "gw"}
    out = [
        StreamInfo(f"rtt.{a}", "rtt", "ms", hz, "probe", f"RTT · {names[a]}", anchor=a,
                   stab_tol=0.5)  # fmt: skip
        for a in anchor_ids
    ]
    out += [
        StreamInfo("link.rssi", "rssi", "dBm", hz / 10, "link", "Link RSSI", resample="hold"),
        StreamInfo("link.quality", "quality", "%", hz / 10, "link", "Link quality",
                   resample="hold"),  # fmt: skip
        StreamInfo("link.rate", "rate", "Mbps", hz / 10, "link", "PHY rx rate", resample="hold",
                   stab_tol=50.0),  # fmt: skip
        StreamInfo("drv.retry", "retry", "log10/s", hz / 5, "driver", "Retry counter rate",
                   stab_tol=0.05),  # fmt: skip
        StreamInfo("drv.tx", "tx", "frames/s", hz / 5, "driver", "Tx frames", stab_tol=50),
        StreamInfo("drv.fail", "fail", "frames/s", hz / 5, "driver", "Failed frames", stab_tol=1),
        StreamInfo("truth.state", "truth", "state", hz / 10, "truth", "True state",
                   resample="hold"),  # fmt: skip
    ]
    return out


class SimStepper:
    """Drives a HomeSim at `hz` and turns each tick into samples."""

    def __init__(self, sim: HomeSim, hz: float = 100.0) -> None:
        self.sim = sim
        self.hz = hz
        self.dt = 1.0 / hz
        self.tick = 0
        self.drv_t: float | None = None

    def step(self, t: float) -> list[Sample]:
        sim = self.sim
        sim.advance(self.dt)
        out = [Sample(f"rtt.{a}", t, sim.ping(a)) for a in sim.anchor_ids]
        sim.background_tx(self.dt)
        if self.tick % 5 == 0:
            c = sim.take_counters()
            if self.drv_t is not None:
                span = t - self.drv_t
                out.append(Sample("drv.retry", t, math.log10(1.0 + c["retry"] / span)))
                out.append(Sample("drv.tx", t, c["tx"] / span))
                out.append(Sample("drv.fail", t, c["failed"] / span))
            self.drv_t = t
        if self.tick % 10 == 0:
            r = sim.rssi()
            out.append(Sample("link.rssi", t, float(r)))
            out.append(Sample("link.quality", t, float(min(100, max(0, 2 * (r + 100))))))
            out.append(Sample("link.rate", t, sim.phy_rate()))
            out.append(Sample("truth.state", t, float(STATE_CODE[sim.state])))
        self.tick += 1
        return out


class SimSource(Source):
    kind = "sim"
    name = "simulator"

    def __init__(self, seed: int = 7, hz: float = 100.0, rssi_refresh_s: float = 1.0) -> None:
        super().__init__()
        self.sim = HomeSim(seed=seed, rssi_refresh_s=rssi_refresh_s)
        self.hz = hz

    def streams(self) -> list[StreamInfo]:
        return sim_streams(self.sim.anchor_ids, self.hz)

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "simulated": True,
            "layout": self.sim.layout.to_dict(),
            "rssi_refresh_s": self.sim.rssi_refresh_s,
        }

    def truth(self) -> dict[str, Any]:
        return self.sim.truth()

    def start(self, emit: Emit) -> None:
        self._stop.clear()
        stepper = SimStepper(self.sim, self.hz)

        def tick() -> None:
            for s in stepper.step(now()):
                emit(s)

        self._spawn(lambda: self._every(self.hz, tick), "sim")


def simulate(
    duration_s: float,
    seed: int = 7,
    hz: float = 100.0,
    schedule: list[tuple[str, float]] | None = None,
    load: bool = True,
) -> tuple[list[Sample], list[dict[str, Any]]]:
    """Offline run on a synthetic clock. Returns samples and the ground-truth label
    intervals. `schedule` forces a sequence of (state, seconds) instead of the random
    scenario; `load=False` switches off household traffic bursts."""
    sim = HomeSim(seed=seed, load=load)
    stepper = SimStepper(sim, hz)
    plan = list(schedule or [])
    plan_end = 0.0
    if plan:
        state, secs = plan.pop(0)
        sim.set_mode(state)
        plan_end = secs
    samples: list[Sample] = []
    labels: list[dict[str, Any]] = []
    cur, cur_start = sim.state, 0.0
    n = int(duration_s * hz)
    for i in range(n):
        t = i / hz
        if plan and t >= plan_end:
            state, secs = plan.pop(0)
            sim.set_mode(state)
            plan_end += secs
        samples.extend(stepper.step(t))
        if sim.state != cur:
            labels.append({"start": cur_start, "end": t, "label": cur})
            cur, cur_start = sim.state, t
    labels.append({"start": cur_start, "end": n / hz, "label": cur})
    return samples, labels
