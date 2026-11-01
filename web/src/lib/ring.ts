// Fixed-capacity time series held in typed arrays. Canvases read these directly
// every animation frame; React never sees the samples.

export class Ring {
  readonly t: Float64Array;
  readonly v: Float64Array;
  private head = 0; // index of the oldest sample
  size = 0;

  constructor(readonly capacity: number) {
    this.t = new Float64Array(capacity);
    this.v = new Float64Array(capacity);
  }

  push(t: number, v: number | null): void {
    const i = (this.head + this.size) % this.capacity;
    this.t[i] = t;
    this.v[i] = v === null ? NaN : v;
    if (this.size < this.capacity) this.size++;
    else this.head = (this.head + 1) % this.capacity;
  }

  clear(): void {
    this.head = 0;
    this.size = 0;
  }

  /** Physical index of the k-th oldest sample. */
  at(k: number): number {
    return (this.head + k) % this.capacity;
  }

  /** Logical index of the first sample with t >= t0 (binary search). */
  lowerBound(t0: number): number {
    let lo = 0;
    let hi = this.size;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.t[this.at(mid)] < t0) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  last(): number | null {
    if (!this.size) return null;
    for (let k = this.size - 1; k >= 0 && k >= this.size - 50; k--) {
      const v = this.v[this.at(k)];
      if (!Number.isNaN(v)) return v;
    }
    return null;
  }

  /** Finite values with t >= t0, for scale estimation. */
  valuesSince(t0: number): number[] {
    const out: number[] = [];
    for (let k = this.lowerBound(t0); k < this.size; k++) {
      const v = this.v[this.at(k)];
      if (!Number.isNaN(v)) out.push(v);
    }
    return out;
  }
}

export function quantile(sorted: number[], q: number): number {
  if (!sorted.length) return NaN;
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
