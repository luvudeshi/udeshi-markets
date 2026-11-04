"use client";

import { Empty, Panel } from "@/components/ui/Panel";
import { fmtPercent, fmtSigned, fmtSimTime } from "@/lib/format";
import { useTerminal } from "@/lib/stream/store";
import type { NewsOut } from "@/lib/types";

const SENTIMENT: Record<NewsOut["sentiment"], { mark: string; className: string; label: string }> =
  {
    positive: { mark: "▲", className: "text-bid", label: "Positive" },
    negative: { mark: "▼", className: "text-ask", label: "Negative" },
    neutral: { mark: "•", className: "text-muted", label: "Neutral" },
  };

/**
 * Published news, newest first, with the change each item made to the instrument's fundamental
 * value, as the backend reports it (unannounced shocks never appear here).
 */
export function NewsFeed({ className = "" }: { className?: string }) {
  const news = useTerminal((state) => state.news);
  const selected = useTerminal((state) => state.selectedSymbol);
  const items = [...news].reverse();

  return (
    <Panel
      title="News"
      className={className}
      testId="news"
      actions={
        news.length > 0 ? (
          <span className="label-caps num" data-testid="news-count">
            {news.length}
          </span>
        ) : undefined
      }
    >
      {items.length === 0 ? (
        <Empty>No news published yet</Empty>
      ) : (
        <ol aria-label="News">
          {items.map((item, index) => {
            const sentiment = SENTIMENT[item.sentiment];
            const emphasised = item.symbol === selected;
            return (
              <li
                key={`${item.time_seconds}-${item.symbol}-${index}`}
                data-testid="news-item"
                data-symbol={item.symbol}
                data-emphasised={emphasised}
                className={`border-b border-line/60 px-2.5 py-1.5 ${
                  emphasised ? "bg-raised shadow-[inset_2px_0_0_var(--color-accent)]" : ""
                }`}
              >
                <div className="flex items-baseline gap-2 text-[10.5px]">
                  <span className="num text-muted">{fmtSimTime(item.time_seconds)}</span>
                  <span className={`font-medium ${emphasised ? "text-text" : "text-text/75"}`}>
                    {item.symbol}
                  </span>
                  <span
                    className={sentiment.className}
                    title={sentiment.label}
                    aria-label={sentiment.label}
                  >
                    {sentiment.mark}
                  </span>
                  <span
                    className={`num ml-auto ${sentiment.className}`}
                    title={`Change in the fundamental value (log impact ${fmtSigned(item.impact, 4)})`}
                  >
                    {fmtPercent(item.change_percent)}
                  </span>
                </div>
                <p
                  className={`mt-0.5 text-[11px] leading-snug ${
                    emphasised ? "text-text" : "text-text/70"
                  }`}
                >
                  {item.headline}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}
