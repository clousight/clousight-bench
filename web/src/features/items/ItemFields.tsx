/**
 * One item's own fields, under its row.
 *
 * **Absence, pointers and content are three different things, and this file
 * refuses to collapse them.** `ItemResult.input/output/reference` may hold a
 * value directly OR a `{"$artifact": "<key-or-relpath>"}` pointer for blobs too
 * large to sit in the record — a swe-bench patch, a rendered image. The viewer
 * has no artifact-reading capability, so a pointer is rendered AS a pointer,
 * with the path a reader needs to go find it. Rendering it as content would be
 * a lie; rendering it as empty would be a different one.
 *
 * **Long values scroll, they do not truncate.** An ellipsis on an output the
 * reader came here to read is the worst of both: it hides the tail and looks
 * complete. A capped, scrollable `<pre>` costs the same vertical space and
 * keeps every character reachable — and copyable.
 */

import { CopyButton } from "@/components/CopyButton";
import { useI18n } from "@/i18n";
import { itemStatus, type ItemResultData } from "@/lib/items";

export interface RenderedValue {
  kind: "empty" | "scalar" | "artifact" | "blob";
  text: string;
}

/** An `{"$artifact": "..."}` pointer's path, or null if this is not one. */
function artifactPath(value: unknown): string | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const path = (value as Record<string, unknown>).$artifact;
  return typeof path === "string" ? path : null;
}

/**
 * Classify a value for display. See the table in the design's D6b.
 *
 * Strings come back whole: the caller caps the height, not the content.
 */
export function renderValue(value: unknown): RenderedValue {
  if (value === null || value === undefined) return { kind: "empty", text: "" };
  const pointer = artifactPath(value);
  if (pointer !== null) return { kind: "artifact", text: pointer };
  if (typeof value === "string") return { kind: "blob", text: value };
  if (typeof value === "number" || typeof value === "boolean") {
    return { kind: "scalar", text: String(value) };
  }
  return { kind: "blob", text: JSON.stringify(value, null, 2) };
}

/** Whether this item has anything worth expanding to. */
export function hasFields(item: ItemResultData): boolean {
  for (const key of ["input", "output", "reference"] as const) {
    if (renderValue(item[key]).kind !== "empty") return true;
  }
  if (Object.keys(item.attrs ?? {}).length > 0) return true;
  return (item.scores ?? []).some(
    (score) => (score.reason ?? "") !== "" || (score.error ?? "") !== "",
  );
}

function ValueBlock({ label, value }: { label: string; value: unknown }) {
  const { t } = useI18n();
  const rendered = renderValue(value);
  if (rendered.kind === "empty") return null;

  if (rendered.kind === "artifact") {
    return (
      <div className="flex flex-col gap-1">
        <FieldLabel>{label}</FieldLabel>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
            {t("items.artifact_ref")}
          </span>
          <span className="break-all font-mono text-xs">{rendered.text}</span>
          <CopyButton value={rendered.text} />
        </div>
        <p className="text-xs text-muted-foreground">{t("items.artifact_blurb")}</p>
      </div>
    );
  }

  if (rendered.kind === "scalar") {
    return (
      <div className="flex flex-col gap-1">
        <FieldLabel>{label}</FieldLabel>
        <span className="font-mono text-xs tabular-nums">{rendered.text}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <FieldLabel>{label}</FieldLabel>
        <CopyButton value={rendered.text} />
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-background p-2 font-mono text-xs">
        {rendered.text}
      </pre>
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
      {children}
    </span>
  );
}

export function ItemFields({ item }: { item: ItemResultData }) {
  const { t } = useI18n();
  const attrs = item.attrs ?? {};
  const annotated = (item.scores ?? []).filter(
    (score) => (score.reason ?? "") !== "" || (score.error ?? "") !== "",
  );

  return (
    <div className="flex flex-col gap-3 py-1">
      <ValueBlock label={t("items.field_input")} value={item.input} />
      <ValueBlock label={t("items.field_output")} value={item.output} />
      <ValueBlock label={t("items.field_reference")} value={item.reference} />
      {Object.keys(attrs).length > 0 && (
        <ValueBlock label={t("items.field_attrs")} value={attrs} />
      )}
      {annotated.map((score) => {
        const isError = itemStatus(score) === "error";
        const body = isError ? (score.error ?? "") : (score.reason ?? "");
        return (
          <div key={`${score.metric}-${isError ? "error" : "reason"}`} className="flex flex-col gap-1">
            <FieldLabel>
              {score.metric} · {isError ? t("items.status.error") : t("items.reason")}
            </FieldLabel>
            <p
              className={
                isError ? "text-xs text-status-serious" : "text-xs text-muted-foreground"
              }
            >
              {body}
            </p>
          </div>
        );
      })}
    </div>
  );
}
