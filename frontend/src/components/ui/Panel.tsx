import type { ReactNode } from "react";

/** A titled region of the terminal: a thin header strip over a body that scrolls on its own. */
export function Panel({
  title,
  actions,
  children,
  className = "",
  bodyClassName = "",
  testId,
}: {
  title: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
}) {
  return (
    <section
      className={`flex min-h-0 min-w-0 flex-col bg-panel ${className}`}
      data-testid={testId}
      aria-label={typeof title === "string" ? title : undefined}
    >
      <header className="flex h-7 shrink-0 items-center justify-between gap-2 border-b border-line px-2.5">
        {typeof title === "string" ? (
          <h2 className="label-caps truncate text-text/80">{title}</h2>
        ) : (
          title
        )}
        {actions !== undefined && <div className="flex items-center gap-1">{actions}</div>}
      </header>
      <div className={`min-h-0 flex-1 overflow-auto ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/** Placeholder text for a panel with nothing to show yet. */
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-16 items-center justify-center px-3 text-center text-muted">
      {children}
    </div>
  );
}
