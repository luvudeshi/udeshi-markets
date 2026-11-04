"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import { Button, Field, TextInput } from "@/components/ui/controls";
import { useApi } from "@/lib/api-context";
import { fmtInt } from "@/lib/format";
import { useTerminal } from "@/lib/stream/store";

import { useControls } from "./controls-store";
import { describeError, type ErrorDescription } from "@/lib/errors";
import {
  AGENT_FIELDS,
  DEFAULT_FORM,
  DEFAULT_PAIR,
  MARKET_FIELDS,
  RUN_FIELDS,
  agentTotal,
  toRequest,
  type FieldKey,
  type FieldSpec,
  type FormValues,
} from "./simulation-form";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="min-w-0 border-b border-line px-4 pt-3 pb-4 last:border-b-0">
      {/* Floated so it lays out as an ordinary heading, not inside the fieldset's border. */}
      <legend className="label-caps float-left mb-2.5 w-full font-semibold text-text">
        {title}
      </legend>
      <div className="clear-both">{children}</div>
    </fieldset>
  );
}

function NumberField({
  spec,
  value,
  onChange,
}: {
  spec: FieldSpec;
  value: string;
  onChange: (key: FieldKey, value: string) => void;
}) {
  return (
    <Field label={spec.label} hint={spec.hint}>
      <TextInput
        type="number"
        inputMode="decimal"
        name={spec.key}
        min={spec.min}
        max={spec.max}
        step={spec.integer ? 1 : "any"}
        value={value}
        onChange={(event) => onChange(spec.key, event.target.value)}
        className="invalid:border-ask/70"
        title={spec.hint}
      />
    </Field>
  );
}

/**
 * Configures and creates a simulation: run settings, the agent population and the market
 * environment. A native modal <dialog>: the page behind it is inert, focus stays inside, and
 * Escape closes it. Entered values are kept between openings.
 */
export function NewSimulationDialog() {
  const api = useApi();
  const open = useControls((state) => state.dialogOpen);
  const dialog = useRef<HTMLDialogElement>(null);
  const [values, setValues] = useState<FormValues>(DEFAULT_FORM);
  const [error, setError] = useState<ErrorDescription | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const element = dialog.current;
    if (element === null) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);

  const set = (key: FieldKey, value: string) =>
    setValues((previous) => ({ ...previous, [key]: value }));
  const total = agentTotal(values);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = toRequest(values);
    if ("problems" in result) {
      const message = "Some fields are not numbers";
      setError({ code: "invalid_input", message, title: message, lines: result.problems });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSimulation(result.request);
      const controls = useControls.getState();
      useTerminal.getState().setSimulation(created.id);
      controls.simulationsChanged();
      controls.closeDialog();
      if (values.startNow) {
        try {
          await api.lifecycle(created.id, "start");
        } catch (startError) {
          controls.notify(startError);
        }
      }
    } catch (createError) {
      setError(describeError(createError));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      onClose={() => {
        useControls.getState().closeDialog();
        setError(null);
      }}
      aria-labelledby="new-simulation-title"
      data-testid="new-simulation-dialog"
      className="m-auto max-h-[calc(100dvh-32px)] w-[min(820px,calc(100vw-24px))] overflow-hidden border border-line-strong bg-panel p-0 text-xs text-text backdrop:bg-black/65"
    >
      <form noValidate onSubmit={submit} className="flex max-h-[calc(100dvh-34px)] flex-col">
        <header className="flex h-9 shrink-0 items-center justify-between border-b border-line px-4">
          <h2 id="new-simulation-title" className="text-[12px] font-semibold tracking-wide">
            New simulation
          </h2>
          <div className="flex items-center gap-2">
            <span className="label-caps text-faint">Esc to close</span>
            <Button
              tone="ghost"
              aria-label="Close"
              onClick={() => useControls.getState().closeDialog()}
              className="w-6 px-0"
            >
              ✕
            </Button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto md:grid-cols-2 md:divide-x md:divide-line">
          <div className="min-w-0">
            <Section title="Run">
              <div className="grid grid-cols-3 items-end gap-x-3 gap-y-2.5">
                {RUN_FIELDS.map((spec) => (
                  <NumberField key={spec.key} spec={spec} value={values[spec.key]} onChange={set} />
                ))}
              </div>
            </Section>
            <Section title="Agents">
              <div className="grid grid-cols-3 items-end gap-x-3 gap-y-2.5">
                {AGENT_FIELDS.map((spec) => (
                  <NumberField key={spec.key} spec={spec} value={values[spec.key]} onChange={set} />
                ))}
              </div>
              <p className="mt-2.5 text-muted">
                <span className="num text-text" data-testid="agent-total">
                  {total === null ? "—" : fmtInt(total)}
                </span>{" "}
                agents in total (at most 500), each assigned an instrument in turn; pairs traders
                trade a pair.
              </p>
            </Section>
          </div>
          <div className="min-w-0">
            <Section title="Market environment">
              <div className="grid grid-cols-2 items-end gap-x-3 gap-y-2.5">
                {MARKET_FIELDS.map((spec) => (
                  <NumberField key={spec.key} spec={spec} value={values[spec.key]} onChange={set} />
                ))}
              </div>
              <label className="mt-3 flex items-center gap-2">
                <input
                  type="checkbox"
                  name="pair"
                  checked={values.pair}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, pair: event.target.checked }))
                  }
                  className="size-3.5 accent-accent"
                />
                <span>
                  {DEFAULT_PAIR.leader}/{DEFAULT_PAIR.follower} pair
                  <span className="text-muted">
                    {" "}
                    — {DEFAULT_PAIR.follower} follows {DEFAULT_PAIR.leader} with mean-reverting
                    deviations
                  </span>
                </span>
              </label>
              <p className="mt-2 text-muted">
                Rates are per hour of simulated time; impacts are changes in log value (0.01 is
                about 1%). Fundamental values stay hidden from the agents.
              </p>
            </Section>
          </div>
        </div>

        {error !== null && (
          <div
            role="alert"
            data-testid="new-simulation-error"
            className="shrink-0 border-t border-ask/40 bg-ask-soft px-4 py-2 text-ask"
          >
            <p className="font-medium">{error.title}</p>
            {error.lines.length > 0 && (
              <ul className="num mt-1 list-none space-y-0.5 text-[11px]">
                {error.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        <footer className="flex h-11 shrink-0 items-center justify-between gap-3 border-t border-line px-4">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              name="startNow"
              checked={values.startNow}
              onChange={(event) =>
                setValues((previous) => ({ ...previous, startNow: event.target.checked }))
              }
              className="size-3.5 accent-accent"
            />
            <span>Start immediately</span>
          </label>
          <div className="flex items-center gap-2">
            <Button tone="ghost" onClick={() => setValues(DEFAULT_FORM)} disabled={busy}>
              Defaults
            </Button>
            <Button onClick={() => useControls.getState().closeDialog()} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" tone="primary" disabled={busy} data-testid="sim-create">
              {busy ? "Creating…" : "Create simulation"}
            </Button>
          </div>
        </footer>
      </form>
    </dialog>
  );
}
