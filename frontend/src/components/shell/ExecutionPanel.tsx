"use client";

import { useState } from "react";

import { OrderTicket } from "@/components/ticket/OrderTicket";
import { TradeTape } from "@/components/tape/TradeTape";
import { Panel } from "@/components/ui/Panel";
import { Tabs } from "@/components/ui/controls";

const TABS = [
  { id: "tape", label: "Trade tape" },
  { id: "ticket", label: "Order entry" },
] as const;

type Tab = (typeof TABS)[number]["id"];

/** The tape and the order ticket share one panel. */
export function ExecutionPanel({ className = "" }: { className?: string }) {
  const [tab, setTab] = useState<Tab>("tape");
  return (
    <Panel
      title={<Tabs tabs={TABS} value={tab} onChange={setTab} label="Executions" />}
      className={className}
      testId="executions"
    >
      {tab === "tape" ? <TradeTape /> : <OrderTicket />}
    </Panel>
  );
}
