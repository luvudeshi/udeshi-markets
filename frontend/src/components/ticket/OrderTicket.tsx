"use client";

import { useId, useState, type FormEvent } from "react";

import { Button, Field, Segmented, TextInput } from "@/components/ui/controls";
import { Empty } from "@/components/ui/Panel";
import { useApi } from "@/lib/api-context";
import { decimalsOf, fmtNumber } from "@/lib/format";
import { useIsRunning, useMoment, usePoll } from "@/lib/hooks";
import type { MarketState } from "@/lib/stream/reducer";
import { useSelectedMarket, useTerminal } from "@/lib/stream/store";
import type { OrderIn, OrderOut } from "@/lib/types";

import { describeError } from "@/lib/errors";
import { OpenOrders } from "./OpenOrders";
import { TicketResult, type Outcome } from "./TicketResult";

type Side = OrderIn["side"];
type OrderType = NonNullable<OrderIn["type"]>;
type TimeInForce = NonNullable<OrderIn["time_in_force"]>;

const TYPES: readonly OrderType[] = ["limit", "market"];
const TIME_IN_FORCE: readonly TimeInForce[] = ["gtc", "ioc"];

// Input parsing only (is this a well-formed number?); the API validates everything else.
const QUANTITY = /^\d+$/;
const PRICE = /^(\d+\.?\d*|\.\d+)$/;

function SideToggle({ side, onChange }: { side: Side; onChange: (side: Side) => void }) {
  return (
    <div role="radiogroup" aria-label="Side" className="grid grid-cols-2 gap-1">
      {(["buy", "sell"] as const).map((option) => {
        const selected = option === side;
        return (
          <Button
            key={option}
            role="radio"
            aria-checked={selected}
            tone={selected ? (option === "buy" ? "bid" : "ask") : "default"}
            className={`h-7 ${selected ? "" : "text-muted"}`}
            onClick={() => onChange(option)}
          >
            {option === "buy" ? "Buy" : "Sell"}
          </Button>
        );
      })}
    </div>
  );
}

function TicketForm({
  simulationId,
  selected,
  onPlaced,
}: {
  simulationId: string;
  selected: MarketState;
  onPlaced: () => void;
}) {
  const api = useApi();
  const priceId = useId();
  const [side, setSide] = useState<Side>("buy");
  const [type, setType] = useState<OrderType>("limit");
  const [priceText, setPriceText] = useState("");
  const [quantityText, setQuantityText] = useState("10");
  const [timeInForce, setTimeInForce] = useState<TimeInForce>("gtc");
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  const { symbol, book, market } = selected;
  const decimals = decimalsOf(market.tick_size);
  // The backend's best price on the other side: what a marketable order would trade against.
  const best = side === "buy" ? book.best_ask : book.best_bid;
  const limit = type === "limit";
  const quantityValid = QUANTITY.test(quantityText) && Number(quantityText) >= 1;
  const priceValid = !limit || (PRICE.test(priceText) && Number(priceText) > 0);
  const ready = !submitting && quantityValid && priceValid;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    const order: OrderIn = limit
      ? {
          side,
          type,
          price: Number(priceText),
          quantity: Number(quantityText),
          time_in_force: timeInForce,
        }
      : { side, type, quantity: Number(quantityText) };
    setSubmitting(true);
    try {
      const placed: OrderOut = await api.submitOrder(simulationId, symbol, order);
      setOutcome({ kind: "order", order: placed });
    } catch (error) {
      setOutcome({ kind: "error", problem: describeError(error) });
    } finally {
      setSubmitting(false);
      onPlaced();
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-2 p-2.5" noValidate>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12px] font-medium text-text">{symbol}</span>
        <span className="num text-[11px]">
          <span className="text-bid">{fmtNumber(book.best_bid, decimals)}</span>
          <span className="text-faint"> / </span>
          <span className="text-ask">{fmtNumber(book.best_ask, decimals)}</span>
        </span>
      </div>
      <SideToggle side={side} onChange={setSide} />
      <div className="flex items-center justify-between gap-2">
        <Segmented
          label="Order type"
          options={TYPES}
          value={type}
          onChange={setType}
          format={(value) => (value === "limit" ? "Limit" : "Market")}
        />
        <Segmented
          label="Time in force"
          options={TIME_IN_FORCE}
          value={timeInForce}
          onChange={setTimeInForce}
          format={(value) => value.toUpperCase()}
          disabled={!limit}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <label htmlFor={priceId} className="label-caps">
            Price
          </label>
          <div className="flex gap-1">
            <TextInput
              id={priceId}
              inputMode="decimal"
              autoComplete="off"
              placeholder={limit ? fmtNumber(best, decimals).replaceAll(",", "") : "Market"}
              value={limit ? priceText : ""}
              disabled={!limit}
              onChange={(event) => setPriceText(event.target.value.trim())}
              aria-invalid={limit && priceText !== "" && !priceValid}
            />
            <Button
              className="h-7 shrink-0"
              disabled={!limit || best === null}
              onClick={() => best !== null && setPriceText(best.toFixed(decimals))}
              aria-label={side === "buy" ? "Use the best ask" : "Use the best bid"}
              title={side === "buy" ? "Best ask" : "Best bid"}
            >
              Best
            </Button>
          </div>
        </div>
        <Field label="Quantity">
          <TextInput
            inputMode="numeric"
            autoComplete="off"
            value={quantityText}
            onChange={(event) => setQuantityText(event.target.value.trim())}
            aria-invalid={quantityText !== "" && !quantityValid}
          />
        </Field>
      </div>
      <Button
        type="submit"
        tone={side === "buy" ? "bid" : "ask"}
        className="h-7 text-[12px]"
        disabled={!ready}
        data-testid="ticket-submit"
      >
        {submitting ? "Sending…" : `${side === "buy" ? "Buy" : "Sell"} ${symbol}`}
      </Button>
      {outcome !== null && <TicketResult outcome={outcome} decimals={decimals} />}
    </form>
  );
}

/**
 * Order entry for the selected market, as the API user (participant 1,000,000): the same
 * kernel path, ownership checks and digest as any agent's orders. Below it, the user's own
 * resting orders.
 */
export function OrderTicket() {
  const api = useApi();
  const simulationId = useTerminal((state) => state.simulationId);
  const symbol = useTerminal((state) => state.selectedSymbol);
  const moment = useMoment();
  const selected = useSelectedMarket();
  const running = useIsRunning();
  const key = simulationId === null || symbol === null ? null : `${simulationId}/${symbol}`;
  const orders = usePoll(
    key,
    () =>
      simulationId === null || symbol === null
        ? Promise.resolve([])
        : api.myOrders(simulationId, symbol),
    running ? 1_000 : null,
    // While paused, fills arrive with steps: fetch again whenever something happened.
    moment,
  );

  if (simulationId === null || selected === undefined) {
    return <Empty>No market selected</Empty>;
  }
  return (
    <div data-testid="order-ticket" className="flex flex-col">
      <TicketForm
        key={`${simulationId}/${selected.symbol}`}
        simulationId={simulationId}
        selected={selected}
        onPlaced={orders.refresh}
      />
      <OpenOrders
        simulationId={simulationId}
        symbol={selected.symbol}
        decimals={decimalsOf(selected.market.tick_size)}
        orders={orders}
      />
    </div>
  );
}
