import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { describeError } from "./errors";
import {
  DEFAULT_FORM,
  MAX_BROWSER_SEED,
  agentTotal,
  parseField,
  toRequest,
} from "./simulation-form";

describe("the new-simulation form", () => {
  it("turns the defaults into the backend's defaults", () => {
    const result = toRequest(DEFAULT_FORM);
    expect(result).toEqual({
      request: {
        seed: 1,
        duration_seconds: 1_800,
        speed: 1,
        initial_cash: 1_000_000,
        order_latency_ms: 0,
        bar_interval_seconds: 1,
        agents: {
          random_traders: 10,
          market_makers: 5,
          momentum_traders: 5,
          fundamental_traders: 5,
          pairs_traders: 1,
          order_frequency: 1,
        },
        market: {
          volatility: 0.02,
          volatile_multiplier: 3,
          news_per_hour: 6,
          news_impact: 0.01,
          shocks_per_hour: 2,
          shock_impact: 0.005,
          liquidity_shocks_per_hour: 2,
          liquidity_shock_quantity: 200,
          pairs: [{ leader: "ACME", follower: "NOVA" }],
        },
      },
    });
    expect(agentTotal(DEFAULT_FORM)).toBe(26);
  });

  it("sends no pairs when the pair is off, and minutes as seconds", () => {
    const result = toRequest({ ...DEFAULT_FORM, pair: false, duration_minutes: "2.5" });
    expect("request" in result && result.request.market?.pairs).toEqual([]);
    expect("request" in result && result.request.duration_seconds).toBe(150);
  });

  it("leaves ranges to the server but refuses text that is not a number", () => {
    // Out of range, but numbers: sent as typed, for the server to judge.
    expect("request" in toRequest({ ...DEFAULT_FORM, duration_minutes: "0" })).toBe(true);
    const result = toRequest({ ...DEFAULT_FORM, speed: "fast", market_makers: " " });
    expect(result).toEqual({
      problems: ["Speed: enter a number", "Market makers: enter a number"],
    });
    expect(agentTotal({ ...DEFAULT_FORM, market_makers: "" })).toBeNull();
    expect([parseField("1e3"), parseField(" 7 "), parseField("Infinity"), parseField("")]).toEqual([
      1000,
      7,
      null,
      null,
    ]);
  });

  it("refuses seeds a browser cannot send exactly", () => {
    const tooBig = String(MAX_BROWSER_SEED + 2);
    expect(toRequest({ ...DEFAULT_FORM, seed: tooBig })).toHaveProperty("problems");
    expect(toRequest({ ...DEFAULT_FORM, seed: "2.5" })).toHaveProperty("problems");
    expect(toRequest({ ...DEFAULT_FORM, seed: String(MAX_BROWSER_SEED) })).toHaveProperty(
      "request",
    );
  });
});

describe("describeError", () => {
  it("lists the server's validation details by field", () => {
    const error = new ApiError(422, "validation_error", "request validation failed", [
      { location: ["body", "duration_seconds"], message: "Input should be greater than 0" },
      { location: ["body", "agents", "market_makers"], message: "Input should be at most 500" },
      "not a detail",
    ]);
    expect(describeError(error)).toEqual({
      title: "request validation failed (validation_error)",
      lines: [
        "duration_seconds: Input should be greater than 0",
        "agents.market_makers: Input should be at most 500",
      ],
    });
  });

  it("describes other failures by their message", () => {
    expect(describeError(new ApiError(409, "invalid_state", "not running"))).toEqual({
      title: "not running (invalid_state)",
      lines: [],
    });
    expect(describeError(new TypeError("Failed to fetch")).title).toBe(
      "Network error: Failed to fetch",
    );
    expect(describeError("boom")).toEqual({ title: "boom", lines: [] });
  });
});
