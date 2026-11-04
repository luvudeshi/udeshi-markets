import type { Metadata } from "next";

import { ExperimentDashboard } from "@/components/experiments/ExperimentDashboard";
import { TopBar } from "@/components/shell/TopBar";

export const metadata: Metadata = { title: "Experiments · Udeshi Markets" };

export default function ExperimentsPage() {
  return (
    <div className="flex h-dvh flex-col">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-auto">
        <ExperimentDashboard />
      </main>
    </div>
  );
}
