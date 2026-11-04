"use client";

import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from "react";

type Tone = "default" | "primary" | "bid" | "ask" | "ghost";

const TONES: Record<Tone, string> = {
  default: "border-line-strong bg-raised text-text hover:bg-hover",
  primary: "border-accent/60 bg-accent-soft text-accent hover:bg-accent/25",
  bid: "border-bid/60 bg-bid-soft text-bid hover:bg-bid/25",
  ask: "border-ask/60 bg-ask-soft text-ask hover:bg-ask/25",
  ghost: "border-transparent bg-transparent text-muted hover:bg-hover hover:text-text",
};

export function Button({
  tone = "default",
  className = "",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone }) {
  return (
    <button
      type={type}
      className={`inline-flex h-6 items-center justify-center gap-1 rounded-[2px] border px-2 text-[11px] font-medium whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${TONES[tone]} ${className}`}
      {...props}
    />
  );
}

/** A row of mutually exclusive options (intervals, speeds, sides). */
export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
  format = String,
  disabled = false,
}: {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  format?: (value: T) => ReactNode;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex rounded-[2px] border border-line-strong"
    >
      {options.map((option) => {
        const selected = option === value;
        return (
          <button
            key={String(option)}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            onClick={() => onChange(option)}
            className={`num h-5 px-1.5 text-[10.5px] transition-colors not-first:border-l not-first:border-line-strong disabled:opacity-40 ${
              selected ? "bg-accent-soft text-accent" : "text-muted hover:bg-hover hover:text-text"
            }`}
          >
            {format(option)}
          </button>
        );
      })}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="label-caps" title={hint}>
        {label}
      </span>
      {children}
    </label>
  );
}

const INPUT =
  "num h-7 w-full min-w-0 rounded-[2px] border border-line-strong bg-bg px-2 text-[11.5px] text-text placeholder:text-faint focus:border-accent/70 focus:outline-none disabled:opacity-50";

export function TextInput({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={`${INPUT} ${className}`} {...props} />;
}

export function Select({ className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={`${INPUT} pr-6 ${className}`} {...props} />;
}

/** Tabs for switching a panel's content. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: readonly { id: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex h-full items-stretch gap-3">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          aria-selected={tab.id === value}
          onClick={() => onChange(tab.id)}
          className={`label-caps -mb-px border-b transition-colors ${
            tab.id === value ? "border-accent text-text" : "border-transparent hover:text-text"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
