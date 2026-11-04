/**
 * Names for the API's data types. Everything here is generated from the backend's OpenAPI
 * schema (`api-types.ts`, see `npm run generate:api`), so a change on the server that the
 * frontend does not follow fails the type check.
 */
import type { components } from "./api-types";

type Schemas = components["schemas"];

export type Health = Schemas["Health"];
export type SimulationCreate = Schemas["SimulationCreate"];
export type SimulationOut = Schemas["SimulationOut"];
export type SimulationState = SimulationOut["state"];
export type AgentsIn = Schemas["AgentsIn"];
export type MarketIn = Schemas["MarketIn"];
export type PairIn = Schemas["PairIn"];
export type InstrumentIn = Schemas["InstrumentIn"];

export type MarketOut = Schemas["MarketOut"];
export type MarketDetailOut = Schemas["MarketDetailOut"];
export type OrderBookOut = Schemas["OrderBookOut"];
export type LevelOut = Schemas["LevelOut"];
export type TradeOut = Schemas["TradeOut"];
export type BarOut = Schemas["BarOut"];

export type OrderIn = Schemas["OrderIn"];
export type OrderOut = Schemas["OrderOut"];
export type OpenOrderOut = Schemas["OpenOrderOut"];
export type CancelOut = Schemas["CancelOut"];

export type AgentOut = Schemas["AgentOut"];
export type AgentDetailOut = Schemas["AgentDetailOut"];
export type Strategy = AgentOut["strategy"];
export type PositionOut = Schemas["PositionOut"];
export type OrderReportOut = Schemas["OrderReportOut"];
export type NewsOut = Schemas["NewsOut"];

export type AnalyticsOut = Schemas["AnalyticsOut"];
export type MarketMetrics = Schemas["MarketMetrics"];
export type MicrostructureMetrics = Schemas["MicrostructureMetrics"];
export type AgentMetrics = Schemas["AgentMetrics"];

export type ExperimentIn = Schemas["ExperimentIn"];
export type ExperimentOut = Schemas["ExperimentOut"];
export type ExperimentStatus = ExperimentOut["status"];
export type ExperimentDetailOut = Schemas["ExperimentDetailOut"];
export type ExperimentPointOut = Schemas["ExperimentPointOut"];
export type MetricSummaryOut = Schemas["MetricSummaryOut"];
export type RunOut = Schemas["RunOut"];
export type ComparisonOut = Schemas["ComparisonOut"];
export type ComparisonResultOut = Schemas["ComparisonResultOut"];

/** The API user: the participant whose orders the order ticket sends. */
export const MANUAL_TRADER_ID = 1_000_000;
/** The market environment (its trades are the liquidity shocks). */
export const ENVIRONMENT_ID = 999_999;
