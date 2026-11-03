"""Active acoustic sonar: near-ultrasound chirps from the PC's speakers, echoes on its
microphone array.

The speakers play a quiet 18.5 to 22 kHz sweep, repeated every 2048 samples (43 ms),
without pause. Each 43 ms block of microphone audio is correlated with the sweep
(pulse compression), which turns it into a range profile: echo strength against extra
path length, at about 5 cm resolution. The direct speaker-to-mic path is the strongest
peak; its position is locked once and every later profile is aligned to it. Playback
and capture share one audio clock, so the lock holds unless the stream drops samples.

Walls and furniture give the same echo every frame. A slowly updated complex average
of the profile captures them; subtracting it leaves only what changed, so a moving
person stands out as a peak in the residual, at their distance. With two microphones,
the same echo arrives at each mic a little apart in time; that path difference gives a
rough direction.

Measured on a Lenovo all-in-one: Windows' voice processing (echo cancellation) erases
the sweep, so capture has to use a raw path (WDM-KS); the two channels are separate
microphones whose direct paths differ by about 22 cm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.signal import chirp

SPEED_OF_SOUND = 343.0


@dataclass
class SonarConfig:
    fs: int = 48000
    period: int = 2048  # samples per sweep: 42.7 ms, about 23 frames per second
    f0: float = 18500.0
    f1: float = 22000.0
    amplitude: float = 0.5
    taper: int = 96  # samples of fade at each end of the sweep, to avoid clicks
    min_range_m: float = 0.35  # closer than this is the PC itself
    max_range_m: float = 5.0
    bin_m: float = 0.05
    lock_frames: int = 12
    min_snr_db: float = 12.0  # below this the sweep is not heard well enough to trust
    warm_frames: int = 20  # learn the noise floor before reporting anything
    confirm_frames: int = 2  # consecutive detections before a target is shown
    bg_alpha: float = 0.04  # background update per frame (~1 s time constant)
    floor_alpha: float = 0.02
    # Per-bin residual power can be averaged over frames before it is judged (1.0 means
    # no averaging). On a real walk, averaging smeared the moving echo across bins and
    # hurt more than it helped.
    integrate_alpha: float = 1.0
    detect_ratio: float = 3.0  # residual power over its usual level to keep a track
    # Higher bar to start a new track than to keep one. Tuned on a recorded walk in a
    # real bedroom: 6x never started a track on reverberation while the person stood
    # still, and still picked them up at the far wall.
    start_ratio: float = 6.0
    gate_m: float = 0.35  # tracker gate half-width when the track was just confirmed
    gate_growth: float = 1.0  # gate widening per second unconfirmed (walking pace)
    gate_floor: float = 0.35  # weight of peaks far outside the gate
    # Moving versus still. At half a metre even breathing changes the echo a lot, so
    # "the echo changed" cannot mean "moving". What separates them is how fast the
    # echo's phase turns: walking at 0.3 m/s turns it more than a full cycle per 43 ms
    # frame, so consecutive frames are unrelated (decorrelation near 1); breathing turns
    # it a few degrees (near 0). Measured at the tracked distance, averaged briefly.
    move_decorr: float = 0.3  # breathing measured 0.02 to 0.2 in simulation
    decorr_alpha: float = 0.3
    jump_m: float = 0.3  # a bigger frame-to-frame jump is re-acquisition, not movement
    motion_min_share: float = 0.3  # moving for at least this share of a second
    # Fast-change map: at every distance, how much the echo changed since the last
    # frame, smoothed over a few frames and neighbouring distances, against that
    # distance's own quiet level. It does not depend on the tracker holding on, so it
    # still sees a walker at 3-4 m whose echo is too faint to track. A gate on the
    # same decorrelation as above keeps breathing and sway out.
    fast_alpha: float = 0.3  # smoothing over frames (about 0.15 s)
    fast_spread: int = 1  # neighbouring bins summed on each side
    fast_rise: float = 0.01  # quiet level creep per frame: doubles in about 3 s
    fast_fall: float = 0.04  # and step down; the level settles near the 20th percentile
    fast_d0: float = 0.3  # decorrelation where the gate starts to open
    fast_d_span: float = 0.4  # and how much more it needs to open fully
    fast_min_range_m: float = 0.6  # nearer is hands on the keyboard and mouse
    fast_move_ratio: float = 15.0  # map level that marks a frame as moving
    # Presence: slow phase changes of the echo over a sliding window. A standing or
    # sitting person sways and breathes by millimetres; at 20 kHz (1.7 cm wavelength)
    # that turns the echo's phase by tens of degrees, which this window catches while
    # the frame-to-frame detector above does not.
    presence_window: int = 72  # frames, about 3 s
    presence_min_frames: int = 16
    presence_rise: float = 0.0005  # quiet-level creep per frame: doubles in about a minute
    presence_ratio: float = 3.0
    presence_min_range_m: float = 0.5  # nearer than this the PC's own body dominates
    hold_s: float = 1.0  # keep showing a target this long after it was last seen
    max_path_diff_m: float = 0.6  # largest mic-to-mic path difference searched
    # Range is (excess path + speaker-to-mic distance) / 2. On an all-in-one the
    # speakers sit roughly 30-40 cm below the microphones, so add about 0.17 m.
    device_offset_m: float = 0.17


def make_chirp(cfg: SonarConfig) -> np.ndarray:
    t = np.arange(cfg.period) / cfg.fs
    x = chirp(t, cfg.f0, cfg.period / cfg.fs, cfg.f1)
    w = np.ones(cfg.period)
    fade = np.hanning(2 * cfg.taper)
    w[: cfg.taper] = fade[: cfg.taper]
    w[-cfg.taper :] = fade[cfg.taper :]
    return (cfg.amplitude * x * w).astype(np.float32)


@dataclass
class Health:
    status: str  # locking | ok | silent | weak | clipping
    input_db: float
    snr_db: float | None
    message: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "input_db": round(self.input_db, 1),
            "snr_db": None if self.snr_db is None else round(self.snr_db, 1),
            "message": self.message,
        }


@dataclass
class Target:
    r: float  # metres from the PC
    path_diff: float | None  # metres; far mic minus near mic, None with one mic
    strength: float  # residual power over its usual level
    moving: bool  # seen in this frame, not just held
    still: bool = False  # found by the slow presence channel, not by motion


class PresenceMap:
    """Slow-time variance of the complex echo at every distance over a sliding
    window, against each distance's own quiet level.

    The quiet level is tracked by minimum statistics: it drops straight to any lower
    reading and creeps up slowly, so a quiet room sets it within seconds while a person
    who stays put is only absorbed after about a minute.
    """

    def __init__(self, channels: int, K: int, starts: np.ndarray, n_bins: int,
                 cfg: SonarConfig) -> None:  # fmt: skip
        self.cfg = cfg
        self.W = cfg.presence_window
        self.buf = np.zeros((self.W, channels, K), complex)
        self.s1 = np.zeros((channels, K), complex)
        self.s2 = np.zeros(K)
        self.i = 0
        self.n = 0
        self.frames = 0
        self.starts = starts
        self.n_bins = n_bins
        self.floor: np.ndarray | None = None

    def update(self, x: np.ndarray) -> np.ndarray | None:
        """x: gain-normalised profiles (channels, K). Returns per-bin ratio or None."""
        if self.n == self.W:
            old = self.buf[self.i]
            self.s1 -= old
            self.s2 -= np.abs(old[0]) ** 2
        self.buf[self.i] = x
        self.s1 += x
        self.s2 += np.abs(x[0]) ** 2
        self.i = (self.i + 1) % self.W
        self.n = min(self.n + 1, self.W)
        self.frames += 1
        if self.frames % 512 == 0:  # running sums drift; recompute them exactly
            held = self.buf[: self.n]
            self.s1 = held.sum(axis=0)
            self.s2 = (np.abs(held[:, 0]) ** 2).sum(axis=0)
        if self.n < self.cfg.presence_min_frames:
            return None
        var = np.maximum(self.s2 / self.n - np.abs(self.s1[0] / self.n) ** 2, 0.0)
        vb = np.maximum.reduceat(var, self.starts)[: self.n_bins] + 1e-30
        if self.floor is None:
            self.floor = np.full(self.n_bins, float(np.median(vb)))
        else:
            self.floor = np.where(vb < self.floor, vb, self.floor * (1 + self.cfg.presence_rise))
        return vb / self.floor

    def deviation(self) -> np.ndarray:
        """Latest frame minus the window mean, per channel: the slowly moving part."""
        last = self.buf[(self.i - 1) % self.W]
        return last - self.s1 / max(self.n, 1)


@dataclass
class SonarFrame:
    health: Health
    motion: float = math.nan  # share of the last second someone was changing distance
    fast: float = math.nan  # log10(1 + strongest fast-change level): the detector's input
    presence: float = math.nan  # log10 of the strongest slow-change ratio
    target: Target | None = None
    bins: list[float] = field(default_factory=list)  # 0..1 motion intensity per bin
    static_db: list[float] = field(default_factory=list)
    echoes: list[float] = field(default_factory=list)
    direct_diff_m: float | None = None


class RangeProcessor:
    """Turns consecutive `period`-sample blocks of microphone audio into frames."""

    def __init__(self, cfg: SonarConfig, channels: int) -> None:
        self.cfg = cfg
        self.channels = channels
        self.tx = make_chirp(cfg)
        P = cfg.period
        self._ref = np.conj(np.fft.fft(self.tx))
        # Keep positive frequencies only (analytic profile), and only the sweep's band,
        # weighted by a Hann taper across it. A flat band gives range sidelobes at
        # -13 dB; the taper pushes them to about -31 dB, so the huge direct path stops
        # leaking into every distance, for a slightly wider peak (~10 cm).
        freqs = np.fft.fftfreq(P, 1 / cfg.fs)
        band = (freqs >= cfg.f0) & (freqs <= cfg.f1)
        mask = np.zeros(P)
        mask[band] = 2.0 * np.hanning(int(band.sum()) + 2)[1:-1]
        self._mask = mask
        self.samples_per_m = 2 * cfg.fs / SPEED_OF_SOUND  # excess path is twice the range
        self.max_shift = int(math.ceil(cfg.max_path_diff_m / SPEED_OF_SOUND * cfg.fs))
        self.K = int(cfg.max_range_m * self.samples_per_m) + 2 * self.max_shift + 32
        if self.K >= P:
            raise ValueError("max_range_m does not fit in one sweep period")
        self.bin_samples = cfg.bin_m * self.samples_per_m
        self.n_bins = int((cfg.max_range_m - cfg.min_range_m) / cfg.bin_m)
        first = (cfg.min_range_m - cfg.device_offset_m) * self.samples_per_m
        starts = first + np.arange(self.n_bins) * self.bin_samples
        edges = np.round(np.append(starts, starts[-1] + self.bin_samples))
        self.edges = np.clip(edges, 0, self.K).astype(int)
        self.centres = cfg.min_range_m + (np.arange(self.n_bins) + 0.5) * cfg.bin_m
        self.reset()

    def reset(self) -> None:
        self.lag: int | None = None
        self.lock_peak = 0.0
        self._lock_acc: np.ndarray | None = None
        self._lock_n = 0
        self._misses = 0
        self.background: np.ndarray | None = None
        self.floor: np.ndarray | None = None
        self._acc: np.ndarray | None = None
        self._warm = 0
        self._hits = 0
        self.direct_diff_m: float | None = None
        self._smooth_r: float | None = None
        self._smooth_pd: float | None = None
        self._last_seen = -math.inf
        self._last_target: Target | None = None
        self._still_r: float | None = None
        self._still_pd: float | None = None
        self._recent_r: list[float] = []  # raw detected ranges, for jump detection
        self._prev_resid: np.ndarray | None = None
        self._decorr: float | None = None
        self._last_moving_t = -math.inf
        self._moving_hist: list[tuple[float, bool]] = []
        self._fd: np.ndarray | None = None  # smoothed frame-to-frame change per bin
        self._fb: np.ndarray | None = None  # smoothed echo energy per bin
        self._ffloor: np.ndarray | None = None
        self._fn = 0
        self.fast_bins: np.ndarray | None = None
        self.presence: PresenceMap | None = None
        self._t = 0.0

    def range_of_sample(self, k: float) -> float:
        return k / self.samples_per_m + self.cfg.device_offset_m

    def _profiles(self, block: np.ndarray) -> np.ndarray:
        """Complex range profile per channel: (channels, period)."""
        spec = np.fft.fft(block.T, axis=1) * self._ref * self._mask
        return np.fft.ifft(spec, axis=1)

    def _health(self, block: np.ndarray, snr_db: float | None) -> Health:
        rms = float(np.sqrt(np.mean(block.astype(float) ** 2)))
        input_db = 20 * math.log10(rms + 1e-12)
        peak = float(np.abs(block).max())
        if input_db < -85:
            return Health("silent", input_db, snr_db, "The microphone is delivering silence. "
                          "Check the mic mute key or privacy switch.")  # fmt: skip
        if peak > 0.99:
            return Health("clipping", input_db, snr_db, "The microphone is overloading. "
                          "Lower the speaker volume a little.")  # fmt: skip
        if self.lag is None or snr_db is None:
            return Health("locking", input_db, snr_db, "Finding the direct speaker-to-mic path.")
        if snr_db is not None and snr_db < 15:
            return Health("weak", input_db, snr_db, "The sweep is barely heard. Raise the speaker "
                          "volume to about 70% and check the speakers are not muted.")  # fmt: skip
        return Health("ok", input_db, snr_db, None)

    def process(self, block: np.ndarray, dt: float | None = None) -> SonarFrame:
        """`block` is (period, channels) float audio, consecutive with the last one."""
        cfg = self.cfg
        P = cfg.period
        self._t += dt if dt is not None else P / cfg.fs
        prof = self._profiles(block)
        mag0 = np.abs(prof[0])

        if self.lag is None:
            self._lock_acc = mag0 if self._lock_acc is None else self._lock_acc + mag0
            self._lock_n += 1
            if self._lock_n >= cfg.lock_frames:
                mean = self._lock_acc / self._lock_n
                self.lag = int(np.argmax(mean))
                self.lock_peak = float(mean[self.lag])
                if self.channels > 1:
                    idx = (self.lag + np.arange(-self.max_shift, self.max_shift + 1)) % P
                    m1 = np.abs(prof[1])[idx]
                    self.direct_diff_m = abs(int(np.argmax(m1)) - self.max_shift) * (
                        SPEED_OF_SOUND / cfg.fs
                    )
            return SonarFrame(self._health(block, None))

        # Glitch check: if the direct path vanished from its locked position for a
        # while, the stream dropped samples. Relock and relearn the room.
        direct = float(mag0[self.lag])
        if direct < 0.4 * self.lock_peak:
            self._misses += 1
            if self._misses > 10:
                self.reset()
            # Report how well the sweep is heard, so muted speakers read as "weak".
            snr = 20 * math.log10(float(mag0.max()) / (float(np.median(mag0)) + 1e-12))
            self._last_target = None
            self._prev_resid = None
            return SonarFrame(self._health(block, snr))
        self._misses = 0
        snr_db = 20 * math.log10(direct / (float(np.median(mag0)) + 1e-12))
        if snr_db < cfg.min_snr_db:
            # The sweep is barely heard (speakers muted or turned down): anything
            # "detected" now would be noise, so report the problem and nothing else.
            self._last_target = None
            self._smooth_r = self._smooth_pd = self._still_r = self._still_pd = None
            self._prev_resid = None
            return SonarFrame(self._health(block, snr_db), direct_diff_m=self.direct_diff_m)

        idx = (self.lag + np.arange(self.K)) % P
        aligned = prof[:, idx]
        if self.background is None:
            self.background = aligned.copy()
            return SonarFrame(self._health(block, snr_db), direct_diff_m=self.direct_diff_m)
        # The whole profile wobbles together from frame to frame (speaker and mic gain,
        # sub-sample timing). Fit that common complex gain on the direct path, which no
        # person can be inside, and divide it out before comparing with the background;
        # otherwise the wobble of the direct path alone swamps every distant echo.
        ref = slice(0, 13)
        num = np.sum(aligned[:, ref] * np.conj(self.background[:, ref]), axis=1)
        den = np.sum(np.abs(self.background[:, ref]) ** 2, axis=1) + 1e-12
        norm = aligned / (num / den)[:, None]
        resid = norm - self.background
        self.background += cfg.bg_alpha * (norm - self.background)
        edges = self.edges
        if self.presence is None:
            self.presence = PresenceMap(self.channels, self.K, edges[:-1], self.n_bins, cfg)
        pres = self.presence.update(norm)

        # Range and motion come from mic 0 alone; mic 1 is used only for direction, so
        # its copy of an echo (offset by the mic spacing) never shows up as a second
        # target a few centimetres away.
        power = np.abs(resid[0]) ** 2 / (direct**2)
        raw_bins = np.maximum.reduceat(power, edges[:-1])[: self.n_bins]
        self._acc = _ewma_arr(self._acc, raw_bins, cfg.integrate_alpha)
        bins = self._acc

        if self._warm < cfg.warm_frames:
            # Average the first frames into the noise floor before judging anything.
            self.floor = bins.copy() if self.floor is None else self.floor + bins
            self._warm += 1
            if self._warm == cfg.warm_frames:
                self.floor = self.floor / cfg.warm_frames + 1e-12
            return SonarFrame(self._health(block, snr_db), direct_diff_m=self.direct_diff_m)
        assert self.floor is not None
        ratio = bins / self.floor
        fast = self._fast_map(resid[0])
        # Track, don't just pick the loudest bin. Indoors the sweep reverberates for
        # longer than one period, so anyone moving changes the echo at every distance;
        # the person's own echo is the strongest change only some of the time. A
        # person moves continuously, so peaks near the current track are preferred,
        # with a gate that widens the longer the track goes unconfirmed. A much
        # stronger peak elsewhere can still win and move the track.
        score = ratio
        if self._smooth_r is not None:
            sigma = cfg.gate_m + cfg.gate_growth * (self._t - self._last_seen)
            gate = np.exp(-0.5 * ((self.centres - self._smooth_r) / sigma) ** 2)
            score = ratio * (cfg.gate_floor + (1 - cfg.gate_floor) * gate)
        best = int(np.argmax(score))
        bar = cfg.detect_ratio if self._smooth_r is not None else cfg.start_ratio
        hit = score[best] > bar
        self._hits = self._hits + 1 if hit else 0
        detected = self._hits >= cfg.confirm_frames
        quiet = ratio < cfg.detect_ratio
        self.floor[quiet] += cfg.floor_alpha * (bins[quiet] - self.floor[quiet])

        if detected:
            lo, hi = edges[max(0, best - 1)], edges[min(self.n_bins, best + 2)]
            seg = power[lo:hi]
            k = lo + int(np.argmax(seg))
            k_f = k + _parabolic(power, k)
            r = self.range_of_sample(k_f)
            pd = self._path_diff(resid, k) if self.channels > 1 else None
            # A person cannot cover 30 cm in one 43 ms frame. A jump that big is the
            # tracker re-acquiring on a different echo: start a fresh track there rather
            # than sliding the dot across the room or counting the jump as movement.
            jumped = bool(self._recent_r) and abs(r - float(np.median(self._recent_r))) > cfg.jump_m
            if jumped:
                self._recent_r.clear()
                self._smooth_r = self._smooth_pd = None
            self._recent_r = (self._recent_r + [r])[-5:]
            self._smooth_r = _ewma(self._smooth_r, r, 0.35)
            if pd is not None:
                self._smooth_pd = _ewma(self._smooth_pd, pd, 0.25)
            self._last_seen = self._t
            # On a jump the previous frame's residual belongs to a different echo, so
            # comparing them says nothing about how fast this one is turning.
            d = None if jumped else self._decorrelation(resid[0], lo, hi)
            if d is not None:
                self._decorr = _ewma(self._decorr, min(d, 1.0), cfg.decorr_alpha)
            moving = (self._decorr is not None and self._decorr > cfg.move_decorr) or (
                self._fast_near(self._smooth_r) > cfg.fast_move_ratio
            )
            self._last_target = Target(self._smooth_r, self._smooth_pd, float(ratio[best]),
                                       moving=moving, still=not moving)  # fmt: skip
        elif (
            self._last_target
            and self._t - self._last_seen <= cfg.hold_s
            and self._smooth_r is not None
        ):
            lt = self._last_target
            self._last_target = Target(lt.r, lt.path_diff, lt.strength, False, still=lt.still)
        else:
            self._last_target = None
            self._smooth_r = self._smooth_pd = None
            self._recent_r.clear()
            self._decorr = None

        # Motion level for the detector: the share of the last second in which someone
        # was moving (distance changing). Breathing and fidgeting never raise it.
        self._prev_resid = resid[0].copy()
        now_moving = bool(self._last_target and self._last_target.moving) or (
            math.isfinite(fast) and fast > cfg.fast_move_ratio
        )
        self._moving_hist.append((self._t, now_moving))
        self._moving_hist = [(t, m) for t, m in self._moving_hist if self._t - t <= 1.0]
        share = sum(m for _, m in self._moving_hist) / len(self._moving_hist)
        # A hand on the mouse or a shift in the chair moves for a frame or two; only
        # movement filling a good part of the second counts as motion.
        motion = share if share >= cfg.motion_min_share else 0.0

        # No walking target: fall back to the slow presence channel, which still sees
        # someone who has stopped, by their sway and breathing.
        presence = math.nan
        if pres is not None:
            valid = self.centres >= cfg.presence_min_range_m
            pr = np.where(valid, pres, 0.0)
            pb = int(np.argmax(pr))
            presence = math.log10(float(pr[pb]) + 1e-12)
            lt = self._last_target
            if lt is not None and lt.moving:
                self._last_moving_t = self._t
            if lt is not None and not lt.moving and self._t - self._last_moving_t > 3.0:
                # Nothing has moved for a while, so the fast tracker is following
                # leftovers (reverberation, an old echo). A still person is the
                # presence channel's job: use it, or show nothing. A walker who only
                # slowed down for a moment keeps their track.
                self._last_target = None
            if self._last_target is None and pr[pb] > cfg.presence_ratio:
                k = int(self.edges[pb] + self.bin_samples / 2)
                pd = self._path_diff(self.presence.deviation(), k) if self.channels > 1 else None
                self._still_r = _ewma(self._still_r, float(self.centres[pb]), 0.2)
                if pd is not None:
                    self._still_pd = _ewma(self._still_pd, pd, 0.2)
                self._last_target = Target(self._still_r, self._still_pd, float(pr[pb]),
                                           moving=False, still=True)  # fmt: skip
            elif self._last_target is None or not self._last_target.still:
                self._still_r = self._still_pd = None

        scale = math.log10(cfg.detect_ratio * 4)
        intensity = np.clip(np.log10(np.maximum(ratio, 1.0)) / scale, 0.0, 1.0)
        bg_mag = np.abs(self.background[0])
        static = np.maximum.reduceat(bg_mag, edges[:-1])[: self.n_bins] / self.lock_peak
        static_db = 20 * np.log10(static + 1e-9)
        return SonarFrame(
            health=self._health(block, snr_db),
            motion=motion,
            fast=math.log10(1.0 + fast) if math.isfinite(fast) else math.nan,
            presence=presence,
            target=self._last_target,
            bins=[round(float(v), 3) for v in intensity],
            static_db=[round(float(v), 1) for v in static_db],
            echoes=self._echoes(static_db),
            direct_diff_m=self.direct_diff_m,
        )

    def _fast_map(self, r0: np.ndarray) -> float:
        """Update the fast-change map from this frame's residual; return its peak."""
        cfg = self.cfg
        prev = self._prev_resid
        if prev is None:
            return math.nan
        starts, nb = self.edges[:-1], self.n_bins
        diff = np.add.reduceat(np.abs(r0 - prev) ** 2, starts)[:nb]
        both = np.add.reduceat(np.abs(r0) ** 2 + np.abs(prev) ** 2, starts)[:nb]
        if cfg.fast_spread:
            k = np.ones(2 * cfg.fast_spread + 1)
            diff, both = np.convolve(diff, k, "same"), np.convolve(both, k, "same")
        self._fd = _ewma_arr(self._fd, diff, cfg.fast_alpha)
        self._fb = _ewma_arr(self._fb, both, cfg.fast_alpha)
        # The quiet level follows a low percentile of each bin: it steps down faster
        # than it creeps up, so starting the sonar while someone moves does not leave it
        # deaf, and a steady source (a fan) is absorbed within seconds. A plain running
        # minimum sat so low that noise alone read 15x over it.
        # The first second only sets that level, starting from the average.
        self._fn += 1
        if self._fn <= cfg.warm_frames:
            self._ffloor = _ewma_arr(self._ffloor, self._fd, 1.0 / self._fn)
            return math.nan
        assert self._ffloor is not None
        up = self._fd > self._ffloor
        self._ffloor *= np.where(up, 1 + cfg.fast_rise, 1 - cfg.fast_fall)
        d = self._fd / (self._fb + 1e-30)
        gate = np.clip((d - cfg.fast_d0) / cfg.fast_d_span, 0.0, 1.0)
        level = self._fd / (self._ffloor + 1e-30) * gate
        level[self.centres < cfg.fast_min_range_m] = 0.0
        self.fast_bins = level
        return float(level.max())

    def _fast_near(self, r: float | None, half_m: float = 0.4) -> float:
        if r is None or self.fast_bins is None:
            return 0.0
        near = np.abs(self.centres - r) <= half_m
        return float(self.fast_bins[near].max()) if near.any() else 0.0

    def _decorrelation(self, r0: np.ndarray, lo: int, hi: int) -> float | None:
        """How unrelated this frame's residual is to the last one over samples lo:hi:
        0 for an echo whose phase barely turned, about 1 for one that turned a full
        cycle or more (or for pure noise, which is why it is only read at a tracked
        target, where the echo dominates)."""
        prev = self._prev_resid
        if prev is None or hi <= lo:
            return None
        a, b = r0[lo:hi], prev[lo:hi]
        den = float(np.sum(np.abs(a) ** 2 + np.abs(b) ** 2))
        return float(np.sum(np.abs(a - b) ** 2)) / den if den > 0 else None

    def _path_diff(self, resid: np.ndarray, k: int) -> float | None:
        """Mic 1 minus mic 0 arrival difference of the echo at sample k, in metres.

        The whole complex residual pattern around the echo on mic 0 is correlated
        against mic 1. Matching single peaks failed whenever the person turned round:
        the live echo weakens and the background's "ghost" of where they just were
        wins on one mic but not the other. The ghost shifts between mics just like the
        echo does, so correlating the full pattern stays right.
        """
        p0 = np.abs(resid[0]) ** 2
        w = int(self.bin_samples)
        a, b = max(0, k - w), min(self.K, k + w + 1)
        k0 = a + int(np.argmax(p0[a:b]))  # snap to mic 0's own peak
        s, half = self.max_shift, 64
        lo, hi = k0 - half, k0 + half + 1
        if lo - s < 0 or hi + s > self.K:
            return None
        seg0 = resid[0, lo:hi]
        corr = np.abs(np.correlate(resid[1, lo - s : hi + s], seg0, mode="valid"))
        i = int(np.argmax(corr))
        if corr[i] <= 0:
            return None
        d = i - s + _parabolic(corr, i)
        return d * SPEED_OF_SOUND / self.cfg.fs

    def _echoes(self, static_db: np.ndarray) -> list[float]:
        """Distances of the strongest fixed echoes: walls and big furniture."""
        out = []
        for i in range(1, len(static_db) - 1):
            v = static_db[i]
            if v > -32 and v >= static_db[i - 1] and v >= static_db[i + 1]:
                out.append((v, self.cfg.min_range_m + (i + 0.5) * self.cfg.bin_m))
        out.sort(reverse=True)
        return sorted(round(r, 2) for _, r in out[:6])


def _ewma(prev: float | None, x: float, a: float) -> float:
    return x if prev is None else prev + a * (x - prev)


def _ewma_arr(prev: np.ndarray | None, x: np.ndarray, a: float) -> np.ndarray:
    return x.copy() if prev is None else prev + a * (x - prev)


def _parabolic(y: np.ndarray, i: int) -> float:
    if i <= 0 or i >= len(y) - 1:
        return 0.0
    a, b, c = float(y[i - 1]), float(y[i]), float(y[i + 1])
    den = a - 2 * b + c
    if den >= 0:  # not a local maximum: the fit would extrapolate, so don't move
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (a - c) / den))
