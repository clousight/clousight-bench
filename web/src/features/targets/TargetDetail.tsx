/**
 * One target: what it is, what is in it, and what may be done to it.
 *
 * Two honesty rules carry this page.
 *
 * **Redacted is not empty.** When the server had to hide a credential-shaped
 * value it withholds the file's text entirely, and this page says which keys
 * were hidden instead of rendering a blank box. It also drops the edit
 * affordance, because saving a redacted read would write `***` over the real
 * secret.
 *
 * **Uncounted is not zero.** Nothing in a sealed record says which config file
 * the run was handed, so "used by N runs" cannot be computed. The page says
 * so rather than showing a confident 0 that would read as "nothing uses this,
 * safe to delete".
 */

import { useState } from "react";

import { deleteTarget, useJSON, type Meta, type TargetDetailData } from "@/api";
import { Crumbs, IdentityBar, PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import { targetEditHref, targetsHref } from "@/router";

export function TargetDetailBody({
  target,
  writable,
  onDelete,
  busy,
  failure,
}: {
  target: TargetDetailData;
  writable: boolean;
  onDelete?: () => void;
  busy?: boolean;
  failure?: string | null;
}) {
  const { t } = useI18n();
  const hidden = target.redacted.length > 0;
  // The text is the file. Without it there is nothing honest to edit from.
  const editable = writable && target.yaml !== null && target.error === "";

  return (
    <>
      <IdentityBar
        facts={[
          { label: t("target.file"), value: target.filename, mono: true },
          { label: t("target.col_provider"), value: target.provider || "—" },
          { label: t("target.col_mode"), value: target.mode || "—" },
          { label: t("target.col_region"), value: target.region || "—" },
          { label: t("target.size"), value: `${target.size} B`, mono: true },
          {
            label: t("target.usage_title"),
            value: <span className="text-muted-foreground">{t("target.usage_unknown")}</span>,
          },
        ]}
      />
      <p className="pt-2 text-xs text-muted-foreground">{t("target.usage_blurb")}</p>

      {(editable || (writable && !hidden)) && (
        <div className="flex items-center gap-3 pt-4">
          {editable && (
            <a
              href={targetEditHref(target.name)}
              className="rounded-sm border border-border px-3 py-1.5 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("target.edit")}
            </a>
          )}
          {writable && (
            <button
              type="button"
              onClick={onDelete}
              disabled={busy === true}
              className="rounded-sm border border-border px-3 py-1.5 text-sm text-status-critical hover:bg-muted disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("target.delete")}
            </button>
          )}
        </div>
      )}
      {failure !== undefined && failure !== null && (
        <p role="alert" className="pt-3 text-sm text-status-critical">
          {failure}
        </p>
      )}

      {target.error !== "" && (
        <p role="alert" className="pt-4 text-sm text-status-critical">
          {t("target.unparsable")} {target.error}
        </p>
      )}

      {hidden && (
        <section className="pt-6">
          <h2 className="text-sm font-semibold">{t("target.redacted_title")}</h2>
          <p className="max-w-3xl pt-1 text-xs text-muted-foreground">{t("target.redacted_blurb")}</p>
          <ul className="flex flex-col gap-1 pt-3">
            {target.redacted.map((path) => (
              <li key={path} className="font-mono text-xs">
                {path} <span className="text-muted-foreground">{t("target.redacted")}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="pt-6">
        <h2 className="text-sm font-semibold">{t("target.contents")}</h2>
        <p className="pt-1 text-xs text-muted-foreground">
          {hidden ? t("target.redacted_blurb") : t("target.contents_blurb")}
        </p>
        <pre className="mt-3 overflow-x-auto rounded-sm bg-muted/50 p-3 font-mono text-xs leading-relaxed">
          {target.yaml ?? JSON.stringify(target.data, null, 2)}
        </pre>
      </section>
    </>
  );
}

export function TargetDetail({ name }: { name: string }) {
  const { t } = useI18n();
  const detail = useJSON<TargetDetailData>(`api/targets/${encodeURIComponent(name)}`);
  const meta = useJSON<Meta>("api/meta");
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const remove = () => {
    if (!window.confirm(t("target.delete_confirm"))) return;
    setBusy(true);
    setFailure(null);
    deleteTarget(name)
      .then(() => {
        window.location.hash = targetsHref;
      })
      .catch((err: unknown) => {
        // The server's sentence, verbatim: it is the only part that says why.
        setFailure(err instanceof Error ? err.message : String(err));
        setBusy(false);
      });
  };

  return (
    <>
      <Crumbs items={[{ label: t("target.title"), href: targetsHref }, { label: name }]} />
      <PageHeader title={name} eyebrow={`configs/${name}.yaml`} />
      {detail.error !== null && (
        <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
          {detail.error}
        </p>
      )}
      {detail.error === null && detail.data === null && (
        <p className="border-t border-border py-6 text-sm text-muted-foreground">{t("common.loading")}</p>
      )}
      {detail.data !== null && (
        <TargetDetailBody
          target={detail.data}
          writable={meta.data?.write_enabled === true}
          onDelete={remove}
          busy={busy}
          failure={failure}
        />
      )}
    </>
  );
}
