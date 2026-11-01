"""Recorded sessions: one directory per run, samples in Parquet, metadata in JSON.

    data/sessions/<id>/samples.parquet   stream, t (s since start), v
    data/sessions/<id>/meta.json         streams, labels, setup notes, source, timing
    data/sessions.json                   index of all sessions (rebuilt if missing)

Samples are flushed in chunks while recording, so a crash loses seconds, not the run.
"""

from __future__ import annotations

import json
import math
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from radar.sources.base import Sample, StreamInfo

SCHEMA = pa.schema([("stream", pa.string()), ("t", pa.float64()), ("v", pa.float32())])
FLUSH_ROWS = 20_000


def _new_id(prefix: str) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + (f"-{prefix}" if prefix else "")


class Recorder:
    def __init__(
        self,
        store: SessionStore,
        streams: list[StreamInfo],
        source: dict[str, Any],
        t0: float,
        meta: dict[str, Any] | None = None,
        tag: str = "",
    ) -> None:
        self.store = store
        base = self.id = _new_id(tag)
        k = 2
        while (store.sessions_dir / self.id).exists():
            self.id = f"{base}-{k}"
            k += 1
        self.dir = store.sessions_dir / self.id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.t0 = t0
        self.wall0 = time.time()
        self.meta: dict[str, Any] = {
            "id": self.id,
            "created": self.wall0,
            "source": source,
            "streams": [s.to_dict() for s in streams],
            "setup": dict(meta or {}),
            "labels": [],
        }
        self._rows: tuple[list[str], list[float], list[float]] = ([], [], [])
        self._lock = threading.Lock()
        self._writer = pq.ParquetWriter(self.dir / "samples.parquet", SCHEMA)
        self._label: str | None = None
        self._label_start = 0.0
        self.n = 0
        self.last_t = 0.0

    def add(self, s: Sample) -> None:
        rel = s.t - self.t0
        if rel < 0:
            return
        with self._lock:
            self._rows[0].append(s.stream)
            self._rows[1].append(rel)
            self._rows[2].append(s.v)
            self.n += 1
            self.last_t = max(self.last_t, rel)

    def flush(self, force: bool = False) -> None:
        with self._lock:
            if not self._rows[0] or (not force and len(self._rows[0]) < FLUSH_ROWS):
                return
            rows, self._rows = self._rows, ([], [], [])
        table = pa.table({"stream": rows[0], "t": rows[1], "v": rows[2]}, schema=SCHEMA)
        self._writer.write_table(table)

    def elapsed(self, t: float) -> float:
        return t - self.t0

    @property
    def label(self) -> str | None:
        return self._label

    def set_label(self, label: str | None, t: float) -> None:
        """Close the current label interval at t and open a new one (None = unlabelled)."""
        rel = max(0.0, t - self.t0)
        if self._label is not None and rel > self._label_start:
            self.meta["labels"].append(
                {"start": round(self._label_start, 3), "end": round(rel, 3), "label": self._label}
            )
        self._label = label
        self._label_start = rel

    def stop(self, t: float) -> dict[str, Any]:
        self.set_label(None, t)
        self.flush(force=True)
        self._writer.close()
        self.meta["duration"] = round(max(self.last_t, t - self.t0), 3)
        self.meta["n_samples"] = self.n
        self.store.write_meta(self.id, self.meta)
        return self.meta


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.sessions_dir = root / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = root / "sessions.json"

    def _summary(self, meta: dict[str, Any]) -> dict[str, Any]:
        labels = meta.get("labels", [])
        per_label: dict[str, float] = {}
        for lab in labels:
            per_label[lab["label"]] = per_label.get(lab["label"], 0.0) + lab["end"] - lab["start"]
        return {
            "id": meta["id"],
            "created": meta.get("created"),
            "duration": meta.get("duration"),
            "n_samples": meta.get("n_samples"),
            "source": meta.get("source", {}).get("kind"),
            "streams": [s["name"] for s in meta.get("streams", [])],
            "label_seconds": {k: round(v, 1) for k, v in per_label.items()},
            "setup": meta.get("setup", {}),
        }

    def write_meta(self, sid: str, meta: dict[str, Any]) -> None:
        path = self.sessions_dir / sid / "meta.json"
        path.write_text(json.dumps(meta, indent=2, default=_json_default), encoding="utf-8")
        self.rebuild_index()

    def rebuild_index(self) -> list[dict[str, Any]]:
        items = []
        for d in sorted(self.sessions_dir.iterdir()):
            meta = self._read_meta(d.name)
            if meta is not None:
                items.append(self._summary(meta))
        self.index_path.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return items

    def _read_meta(self, sid: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.sessions_dir / sid / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self.rebuild_index()

    def meta(self, sid: str) -> dict[str, Any]:
        meta = self._read_meta(_safe_id(sid))
        if meta is None:
            raise KeyError(sid)
        return meta

    def samples(self, sid: str) -> pd.DataFrame:
        return pd.read_parquet(self.sessions_dir / _safe_id(sid) / "samples.parquet")

    def update(self, sid: str, patch: dict[str, Any]) -> dict[str, Any]:
        meta = self.meta(sid)
        if "labels" in patch:
            meta["labels"] = sorted(
                (
                    {"start": float(x["start"]), "end": float(x["end"]), "label": str(x["label"])}
                    for x in patch["labels"]
                    if float(x["end"]) > float(x["start"])
                ),
                key=lambda x: x["start"],
            )
        if "setup" in patch:
            meta["setup"] = {**meta.get("setup", {}), **patch["setup"]}
        self.write_meta(sid, meta)
        return meta

    def delete(self, sid: str) -> None:
        shutil.rmtree(self.sessions_dir / _safe_id(sid))
        self.rebuild_index()


def _safe_id(sid: str) -> str:
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        raise KeyError(sid)
    return sid


def _json_default(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if hasattr(x, "tolist"):
        return x.tolist()
    raise TypeError(type(x))


def write_session(
    store: SessionStore,
    samples: list[Sample],
    streams: list[StreamInfo],
    labels: list[dict[str, Any]],
    source: dict[str, Any],
    setup: dict[str, Any] | None = None,
    tag: str = "",
) -> str:
    """Write an already-complete run (e.g. an offline simulation) as a session."""
    rec = Recorder(store, streams, source, t0=0.0, meta=setup, tag=tag)
    for s in samples:
        rec.add(s)
    rec.meta["labels"] = [dict(x) for x in labels]
    end = max((s.t for s in samples), default=0.0)
    rec.flush(force=True)
    rec._writer.close()
    rec.meta["duration"] = round(end, 3)
    rec.meta["n_samples"] = rec.n
    store.write_meta(rec.id, rec.meta)
    return rec.id
