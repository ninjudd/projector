/**
 * Caps how many notifications one device gets in a sliding window. It counts
 * in this process's memory, so each function instance keeps its own count and
 * the cap is best-effort, not shared.
 */
export class RateLimit {
  private readonly sent = new Map<string, number[]>();
  private sweptAt: number;

  constructor(
    private readonly limit: number,
    private readonly windowMs: number,
    private readonly now: () => number = Date.now,
  ) {
    this.sweptAt = now();
  }

  /** Counts one notification for `key`, or returns how many milliseconds remain until `key` may send again. */
  take(key: string): number {
    const now = this.now();
    const since = now - this.windowMs;
    if (now - this.sweptAt >= this.windowMs) this.sweep(since, now);
    const times = (this.sent.get(key) ?? []).filter((time) => time > since);
    this.sent.set(key, times);
    const [oldest] = times;
    if (oldest !== undefined && times.length >= this.limit) return oldest + this.windowMs - now;
    times.push(now);
    return 0;
  }

  // Forgets the devices that sent nothing in the window, so memory stays
  // proportional to the devices that sent recently.
  private sweep(since: number, now: number): void {
    for (const [key, times] of this.sent) {
      if ((times.at(-1) ?? -Infinity) <= since) this.sent.delete(key);
    }
    this.sweptAt = now;
  }
}
