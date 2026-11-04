import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { describeError } from "./errors";

describe("describeError", () => {
  it("lists the server's validation details by field", () => {
    const error = new ApiError(422, "validation_error", "request validation failed", [
      { location: ["body", "duration_seconds"], message: "Input should be greater than 0" },
      { location: ["body", "agents", "market_makers"], message: "Input should be at most 500" },
      "not a detail",
    ]);
    expect(describeError(error)).toEqual({
      code: "validation_error",
      message: "request validation failed",
      title: "request validation failed (validation_error)",
      lines: [
        "duration_seconds: Input should be greater than 0",
        "agents.market_makers: Input should be at most 500",
      ],
    });
  });

  it("describes other failures by their message", () => {
    expect(describeError(new ApiError(409, "invalid_state", "not running"))).toEqual({
      code: "invalid_state",
      message: "not running",
      title: "not running (invalid_state)",
      lines: [],
    });
    expect(describeError(new TypeError("Failed to fetch")).title).toBe(
      "Network error: Failed to fetch",
    );
    expect(describeError("boom")).toEqual({
      code: "error",
      message: "boom",
      title: "boom",
      lines: [],
    });
  });
});
