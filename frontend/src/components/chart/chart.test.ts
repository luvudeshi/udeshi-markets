import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { defaultInterval, intervalLabel, intervalOptions } from "./intervals";
import { barStartOf, countPlaced, slotOf, withNewerBars } from "./placement";
import { ThrottledLoader } from "./ThrottledLoader";

describe("intervals", () => {
  it("offers the bar interval and the standard intervals that are whole multiples of it", () => {
    expect(intervalOptions(1)).toEqual([1, 5, 15, 60, 300]);
    expect(intervalOptions(5)).toEqual([5, 15, 60, 300]);
    expect(intervalOptions(0.25)).toEqual([0.25, 1, 5, 15, 60, 300]);
    expect(intervalOptions(0.1)).toEqual([0.1, 1, 5, 15, 60, 300]); // 0.1 is not exact in binary.
    expect(intervalOptions(7)).toEqual([7]); // Nothing standard divides by 7 s.
    expect(intervalOptions(120)).toEqual([120]);
    expect(intervalOptions(0)).toEqual([]);
  });

  it("starts at one second when it can", () => {
    expect(defaultInterval([0.25, 1, 5])).toBe(1);
    expect(defaultInterval([5, 15])).toBe(5);
    expect(defaultInterval([])).toBeNull();
  });

  it("labels intervals", () => {
    expect([0.25, 1, 15, 60, 300, 7200].map(intervalLabel)).toEqual([
      "250ms",
      "1s",
      "15s",
      "1m",
      "5m",
      "2h",
    ]);
  });
});

describe("placement", () => {
  const starts = [10, 15, 20, 25]; // 5-second bars; the last one is still open-ended.

  it("finds the bar containing a time, and how far into it", () => {
    expect(slotOf(starts, 5, 10)).toEqual({ index: 0, fraction: 0 });
    expect(slotOf(starts, 5, 17.5)).toEqual({ index: 1, fraction: 0.5 });
    expect(slotOf(starts, 5, 24.999)?.index).toBe(2);
  });

  it("continues past the last loaded bar, and has no place before the first", () => {
    expect(slotOf(starts, 5, 26)).toEqual({ index: 3, fraction: 0.2 });
    const later = slotOf(starts, 5, 36);
    expect(later?.index).toBe(5);
    expect(later?.fraction).toBeCloseTo(0.2);
    expect(slotOf(starts, 5, 9.99)).toBeNull();
    expect(slotOf([], 5, 12)).toBeNull();
  });

  it("extends the history with the newer streamed bars only", () => {
    const bar = (start_seconds: number, close: number) => ({ start_seconds, close });
    const history = [bar(1, 10), bar(2, 11)];
    expect(withNewerBars(history, [bar(1, 99), bar(2, 99), bar(3, 12), bar(4, 13)])).toEqual([
      bar(1, 10),
      bar(2, 11),
      bar(3, 12),
      bar(4, 13),
    ]);
    expect(withNewerBars(history, [bar(0, 1)])).toBe(history); // Unchanged: the same array.
    expect(withNewerBars([], [bar(5, 1)])).toEqual([bar(5, 1)]);
  });

  it("puts news on the loaded bar that contains it, and nowhere else", () => {
    expect(barStartOf(starts, 5, 21)).toBe(20);
    expect(barStartOf(starts, 5, 29.9)).toBe(25);
    expect(barStartOf(starts, 5, 30)).toBeNull(); // Its bar has not closed yet.
    expect(barStartOf(starts, 5, 3)).toBeNull();
    expect(countPlaced(starts, 5, [3, 10, 22, 40])).toBe(3);
  });
});

describe("ThrottledLoader", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function harness() {
    let calls = 0;
    const pending: ((value: number) => void)[] = [];
    const results: number[] = [];
    const errors: string[] = [];
    const loader = new ThrottledLoader<number>(
      () => {
        calls += 1;
        return new Promise<number>((resolve) => pending.push(resolve));
      },
      1_000,
      (value) => results.push(value),
      (error) => errors.push(error.message),
      () => Date.now(),
    );
    return { loader, pending, results, errors, calls: () => calls };
  }

  it("fetches at once, then at most once per interval, folding requests together", async () => {
    const { loader, pending, results, calls } = harness();
    loader.request();
    expect(calls()).toBe(1);
    loader.request(); // In flight: remembered, not sent.
    loader.request();
    expect(calls()).toBe(1);
    pending[0]?.(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(results).toEqual([1]);
    expect(calls()).toBe(1); // The follow-up waits for the interval.
    await vi.advanceTimersByTimeAsync(999);
    expect(calls()).toBe(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(calls()).toBe(2);
    pending[1]?.(2);
    await vi.advanceTimersByTimeAsync(0);
    expect(results).toEqual([1, 2]);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(calls()).toBe(2); // Nothing more was asked for.
  });

  it("reports failures and discards everything after dispose", async () => {
    const errors: string[] = [];
    const results: number[] = [];
    const failing = new ThrottledLoader<number>(
      () => Promise.reject(new Error("boom")),
      1_000,
      (value) => results.push(value),
      (error) => errors.push(error.message),
    );
    failing.request();
    await vi.advanceTimersByTimeAsync(0);
    expect(errors).toEqual(["boom"]);

    const { loader, pending, results: late, calls } = harness();
    loader.request();
    loader.dispose();
    pending[0]?.(7);
    await vi.advanceTimersByTimeAsync(0);
    expect(late).toEqual([]);
    loader.request();
    expect(calls()).toBe(1);
  });
});
