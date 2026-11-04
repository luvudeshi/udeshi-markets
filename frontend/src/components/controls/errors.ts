import { ApiError } from "@/lib/api";

/** An error as the controls show it: one line, then the server's per-field details. */
export interface ErrorDescription {
  title: string;
  lines: string[];
}

function detailLine(item: unknown): string | null {
  if (typeof item !== "object" || item === null) return null;
  const { location, message } = item as { location?: unknown; message?: unknown };
  if (typeof message !== "string") return null;
  const path = Array.isArray(location) ? location.filter((part) => part !== "body").join(".") : "";
  return path === "" ? message : `${path}: ${message}`;
}

/** The API's error (its message, code and validation details), or any other failure. */
export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    const lines = Array.isArray(error.details)
      ? error.details.map(detailLine).filter((line): line is string => line !== null)
      : [];
    return { title: `${error.message} (${error.code})`, lines };
  }
  if (error instanceof TypeError) {
    // fetch() reports an unreachable server, or a CORS refusal, as a TypeError.
    return { title: `Network error: ${error.message}`, lines: [] };
  }
  return { title: error instanceof Error ? error.message : String(error), lines: [] };
}
