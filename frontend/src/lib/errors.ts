/**
 * Failures as the panels show them, so every one reads the same: the API's stable code and
 * message, then the server's per-field validation details.
 */
import { ApiError } from "./api";

export interface ErrorDescription {
  /** The API's code; "network_error" when no answer arrived, "error" for anything else. */
  code: string;
  /** The API's message, or the failure's own. */
  message: string;
  /** One line for a toast or a heading: "message (code)", or just the message. */
  title: string;
  /** One line per validation detail: "field.path: message". */
  lines: string[];
}

function detailLine(item: unknown): string | null {
  if (typeof item !== "object" || item === null) return null;
  const { location, message } = item as { location?: unknown; message?: unknown };
  if (typeof message !== "string") return null;
  const path = Array.isArray(location) ? location.filter((part) => part !== "body").join(".") : "";
  return path === "" ? message : `${path}: ${message}`;
}

export function describeError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    const lines = Array.isArray(error.details)
      ? error.details.map(detailLine).filter((line): line is string => line !== null)
      : [];
    return {
      code: error.code,
      message: error.message,
      title: `${error.message} (${error.code})`,
      lines,
    };
  }
  if (error instanceof TypeError) {
    // fetch() reports an unreachable server, or a CORS refusal, as a TypeError.
    const message = `Network error: ${error.message}`;
    return { code: "network_error", message, title: message, lines: [] };
  }
  const message = error instanceof Error ? error.message : String(error);
  return { code: "error", message, title: message, lines: [] };
}
