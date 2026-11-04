/** Display helpers used only by the controls. */

const clock = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** A simulation's wall-clock creation time (ISO 8601 from the API) as local HH:MM:SS. */
export function fmtCreatedAt(iso: string): string {
  // Python's isoformat() may give microseconds; Date parses at most milliseconds reliably.
  const date = new Date(iso.replace(/(\.\d{3})\d+/, "$1"));
  return Number.isNaN(date.getTime()) ? iso : clock.format(date);
}

/** 0.5×, 1×, 10×. */
export function fmtSpeed(speed: number): string {
  return `${speed}×`;
}
