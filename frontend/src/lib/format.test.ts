import { describe, expect, it } from "vitest";

import {
  DASH,
  decimalsOf,
  fmtCompact,
  fmtDuration,
  fmtNumber,
  fmtPercent,
  fmtSigned,
  fmtSimTime,
  participantTag,
  signClass,
} from "./format";

describe("format", () => {
  it("finds a tick size's decimal places", () => {
    expect([0.01, 0.05, 0.5, 1, 5, 0.0001, 1e-7, 2.5e-8].map(decimalsOf)).toEqual([
      2, 2, 1, 0, 0, 4, 7, 9,
    ]);
  });

  it("formats numbers, signs and percentages", () => {
    expect(fmtNumber(1234567.891, 2)).toBe("1,234,567.89");
    expect(fmtNumber(null)).toBe(DASH);
    expect(fmtSigned(1.5)).toBe("+1.50");
    expect(fmtSigned(-0.4)).toBe("−0.40");
    expect(fmtSigned(-0.001)).toBe("0.00"); // Rounds to zero: no sign.
    expect(fmtPercent(-1.234)).toBe("−1.23%");
    expect(fmtCompact(950)).toBe("950");
    expect(fmtCompact(12_400)).toBe("12.4K");
    expect(fmtCompact(3_210_000)).toBe("3.21M");
  });

  it("formats simulated time and durations", () => {
    expect(fmtSimTime(0)).toBe("00:00:00");
    expect(fmtSimTime(3723.4567, true)).toBe("01:02:03.457");
    expect(fmtSimTime(59.9996, true)).toBe("00:01:00.000");
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(300)).toBe("5m");
    expect(fmtDuration(5400)).toBe("1h 30m");
  });

  it("colours signs and tags participants", () => {
    expect([signClass(1), signClass(-1), signClass(0), signClass(null)]).toEqual([
      "text-bid",
      "text-ask",
      "text-muted",
      "text-muted",
    ]);
    expect(participantTag({ id: 7, strategy: "random" })).toBe("RND-7");
    expect(participantTag({ id: 999_999, strategy: "environment" })).toBe("ENV");
    expect(participantTag({ id: 1_000_000, strategy: "manual" })).toBe("YOU");
  });
});
