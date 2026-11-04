"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Terminal" },
  { href: "/experiments", label: "Experiments" },
] as const;

function Mark() {
  // Two offset bars: a bid under an ask.
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="1" y="3" width="9" height="3.5" fill="var(--color-ask)" />
      <rect x="6" y="9.5" width="9" height="3.5" fill="var(--color-bid)" />
    </svg>
  );
}

/** The bar across the top of every page: brand, navigation, then the page's own controls. */
export function TopBar({ children }: { children?: ReactNode }) {
  const pathname = usePathname();
  return (
    <header className="flex h-10 shrink-0 items-center gap-4 border-b border-line bg-panel px-3">
      <Link href="/" className="flex items-center gap-2" aria-label="Udeshi Markets home">
        <Mark />
        <span className="text-[11px] font-semibold tracking-[0.2em] text-text">UDESHI</span>
        <span className="-ml-1 text-[11px] tracking-[0.2em] text-muted">MARKETS</span>
      </Link>
      <nav className="flex h-full items-stretch gap-4" aria-label="Main">
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`label-caps flex items-center border-b ${
                active ? "border-accent text-text" : "border-transparent hover:text-text"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="flex min-w-0 flex-1 items-center justify-end gap-2">{children}</div>
    </header>
  );
}
