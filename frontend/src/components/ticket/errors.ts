import { ApiError } from "@/lib/api";

export interface Problem {
  code: string;
  message: string;
}

/**
 * An error as the ticket shows it: the API's stable code and its message. For a validation
 * error the first offending field is named, since the generic message says nothing useful.
 */
export function describeError(error: unknown): Problem {
  if (error instanceof ApiError) {
    const [first] = Array.isArray(error.details) ? (error.details as unknown[]) : [];
    if (typeof first === "object" && first !== null) {
      const detail = first as { location?: unknown; message?: unknown };
      if (typeof detail.message === "string") {
        const field = Array.isArray(detail.location) ? detail.location.at(-1) : undefined;
        const message =
          field === undefined ? detail.message : `${String(field)}: ${detail.message}`;
        return { code: error.code, message };
      }
    }
    return { code: error.code, message: error.message };
  }
  // fetch() itself failed: the API is unreachable, or the browser blocked the request.
  return {
    code: "network_error",
    message: error instanceof Error ? error.message : String(error),
  };
}
