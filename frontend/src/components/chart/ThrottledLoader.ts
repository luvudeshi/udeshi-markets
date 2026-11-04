/**
 * Fetches on request, but never more often than once per ``minIntervalMs`` and never two at a
 * time: requests that arrive meanwhile are folded into one more fetch afterwards. The chart
 * asks for fresh history every time a bar closes, which can be dozens of times a second at
 * high speed.
 */
export class ThrottledLoader<T> {
  private inFlight = false;
  private dirty = false;
  private disposed = false;
  private lastStart = Number.NEGATIVE_INFINITY;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly fetcher: () => Promise<T>,
    private readonly minIntervalMs: number,
    private readonly onResult: (value: T) => void,
    private readonly onError: (error: Error) => void,
    private readonly now: () => number = () => Date.now(),
  ) {}

  request(): void {
    if (this.disposed) return;
    if (this.inFlight) {
      this.dirty = true; // Fetch again once this one is done: it may predate the change.
      return;
    }
    if (this.timer !== null) return; // The scheduled fetch will see the change.
    const wait = this.lastStart + this.minIntervalMs - this.now();
    if (wait > 0) {
      this.timer = setTimeout(() => {
        this.timer = null;
        this.start();
      }, wait);
    } else {
      this.start();
    }
  }

  /** No more fetches, and results still in flight are discarded. */
  dispose(): void {
    this.disposed = true;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private start(): void {
    if (this.disposed) return;
    this.dirty = false;
    this.inFlight = true;
    this.lastStart = this.now();
    this.fetcher()
      .then(
        (value) => {
          if (!this.disposed) this.onResult(value);
        },
        (caught: unknown) => {
          if (!this.disposed) {
            this.onError(caught instanceof Error ? caught : new Error(String(caught)));
          }
        },
      )
      .finally(() => {
        this.inFlight = false;
        if (this.dirty) {
          this.dirty = false;
          this.request();
        }
      });
  }
}
