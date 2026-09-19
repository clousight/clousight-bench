/**
 * The page-level furniture: breadcrumb, header, identity bar, empty state,
 * sticky action bar.
 *
 * These exist so that "where is the title / where is the back link / where do
 * the facts about this object go" has one answer across the app rather than
 * one per view. Every one of them is deliberately dumb — no fetching, no
 * state — so a page can be read top-to-bottom and the layout never surprises.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface Crumb {
  label: string;
  /** Absent on the last crumb: the page you are on is not a link to itself. */
  href?: string;
}

/**
 * Breadcrumb. The final entry is plain text, never a link.
 *
 * A self-link reads as an escape and goes nowhere, which teaches people that
 * the crumbs are decorative.
 */
export function Crumbs({ items }: { items: Crumb[] }) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="breadcrumb" className="flex flex-wrap items-center gap-1.5 text-xs">
      {items.map((crumb, index) => {
        const last = index === items.length - 1;
        return (
          <span key={`${crumb.label}-${index}`} className="flex items-center gap-1.5">
            {index > 0 && (
              <span aria-hidden className="text-muted-foreground/60">
                /
              </span>
            )}
            {last || crumb.href === undefined ? (
              <span aria-current={last ? "page" : undefined} className="text-foreground">
                {crumb.label}
              </span>
            ) : (
              <a
                href={crumb.href}
                className="text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
              >
                {crumb.label}
              </a>
            )}
          </span>
        );
      })}
    </nav>
  );
}

export interface PageHeaderProps {
  title: ReactNode;
  /** A raw id, version or adapter — rendered in mono beside the title. */
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}

/** Title row: heading, an optional mono qualifier, and the page's actions. */
export function PageHeader({ title, eyebrow, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-1.5 pb-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {eyebrow !== undefined && (
          <span className="font-mono text-xs text-muted-foreground">{eyebrow}</span>
        )}
        {actions !== undefined && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>
      {subtitle !== undefined && <p className="max-w-3xl text-sm text-muted-foreground">{subtitle}</p>}
    </div>
  );
}

export interface IdentityFact {
  label: string;
  value: ReactNode;
  /** Renders the value in mono — ids, versions, digests. */
  mono?: boolean;
}

/**
 * The identity bar: what this object IS, as a row of labelled facts.
 *
 * Borrowed wholesale from DataBench's task header, which answers "what was
 * run, against what, by whom, for how long" in one line before any tab is
 * pressed. The previous record page scattered the same facts between the
 * title, a mono line and a section three screens down.
 *
 * Wraps rather than scrolls: a fact pushed off the right edge is a fact the
 * reader will not find, and this row is the page's answer to "what am I
 * looking at".
 */
export function IdentityBar({ facts }: { facts: IdentityFact[] }) {
  if (facts.length === 0) return null;
  return (
    <dl className="flex flex-wrap items-start gap-x-8 gap-y-3 border-y border-border py-3">
      {facts.map((fact, index) => (
        <div key={`${fact.label}-${index}`} className="flex min-w-0 flex-col gap-0.5">
          <dt className="font-mono text-[10px] uppercase leading-none tracking-[0.1em] text-muted-foreground">
            {fact.label}
          </dt>
          <dd className={cn("text-sm", fact.mono === true && "font-mono text-xs")}>{fact.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface EmptyStateProps {
  title: string;
  /**
   * One sentence explaining what this noun IS, not "no data".
   *
   * An empty list is the first thing a new reader sees, and it is the only
   * place in the app where there is room to say what the section is for.
   * DataBench uses it that way — "评测集是一批题。建的时候选一个模板…" — and it
   * is the cheapest documentation in the product.
   */
  blurb?: string;
  action?: ReactNode;
}

export function EmptyState({ title, blurb, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-3 border-t border-border py-16 text-center">
      <p className="text-sm font-medium">{title}</p>
      {blurb !== undefined && <p className="max-w-md text-sm text-muted-foreground">{blurb}</p>}
      {action !== undefined && <div className="pt-1">{action}</div>}
    </div>
  );
}

/**
 * A sticky footer for a form's commit/cancel.
 *
 * Sticky because the forms it serves are long enough to scroll, and a submit
 * button that has scrolled away reads as "there is no way to finish".
 */
export function ActionBar({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 mt-6 flex items-center justify-end gap-2 border-t border-border bg-background/95 py-3 backdrop-blur">
      {children}
    </div>
  );
}

export interface FormSectionProps {
  title: string;
  /** What choosing these fields will cause. Shown under the title. */
  blurb?: string;
  children: ReactNode;
}

/**
 * One block of a form: a title, a consequence, then the fields.
 *
 * The blurb is not optional decoration in spirit. DataBench's create-task form
 * puts a sentence under every required field explaining what the choice
 * freezes — "评测维度由这个评测集当下关联的维度决定，冻结进这次执行的快照。" —
 * and that sentence is what turns a form into something a newcomer can fill in
 * correctly the first time.
 */
export function FormSection({ title, blurb, children }: FormSectionProps) {
  return (
    <section className="border-t border-border pt-4 pb-6">
      <div className="flex flex-col gap-1 pb-4">
        <h2 className="text-sm font-semibold">{title}</h2>
        {blurb !== undefined && <p className="max-w-3xl text-xs text-muted-foreground">{blurb}</p>}
      </div>
      {children}
    </section>
  );
}

export interface FormFieldProps {
  label: string;
  required?: boolean;
  /** What this choice causes — one line, under the control. */
  hint?: string;
  error?: string | null;
  htmlFor?: string;
  children: ReactNode;
}

/** Label, control, consequence, error — in that order, always. */
export function FormField({ label, required, hint, error, htmlFor, children }: FormFieldProps) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={htmlFor} className="flex items-center gap-1 text-xs text-muted-foreground">
        {required === true && (
          <span aria-hidden className="text-status-critical">
            *
          </span>
        )}
        {label}
      </label>
      {children}
      {hint !== undefined && hint !== "" && (
        <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>
      )}
      {error !== undefined && error !== null && error !== "" && (
        <p role="alert" className="text-[11px] leading-snug text-status-critical">
          {error}
        </p>
      )}
    </div>
  );
}
