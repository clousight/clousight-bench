/**
 * Create or edit one target.
 *
 * The form is an explainer, not a set of boxes: every field carries a line
 * saying what the choice causes, because this is where a newcomer learns what
 * a target is. Validation is the pure functions in lib/targets.ts, run before
 * the request so the obvious mistakes never become a round trip — but the
 * server stays the authority on the YAML itself, and when it refuses, its own
 * sentence is what the reader sees.
 *
 * Editing does not rename. A rename here would write a second file and leave
 * the first one on disk; delete-then-create says that out loud, so the name is
 * read-only once the file exists.
 */

import { useState } from "react";

import { putTarget, useJSON, type TargetDetailData } from "@/api";
import { ActionBar, Crumbs, FormField, FormSection, PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import {
  newTargetTemplate,
  targetBodyProblem,
  targetFilename,
  targetNameProblem,
} from "@/lib/targets";
import { targetHref, targetsHref } from "@/router";

export interface FormProblem {
  field: "name" | "yaml";
  code: string;
}

/** The first thing wrong with the form, or null. Name before body. */
export function formProblem(name: string, yaml: string): FormProblem | null {
  const nameProblem = targetNameProblem(name);
  if (nameProblem !== null) return { field: "name", code: nameProblem };
  const bodyProblem = targetBodyProblem(yaml);
  if (bodyProblem !== null) return { field: "yaml", code: bodyProblem };
  return null;
}

export function TargetFormBody({
  mode,
  name,
  yaml,
  problem,
  failure,
  saving,
  onName,
  onYaml,
  onSubmit,
}: {
  mode: "new" | "edit";
  name: string;
  yaml: string;
  problem: FormProblem | null;
  failure?: string | null;
  saving?: boolean;
  onName?: (value: string) => void;
  onYaml?: (value: string) => void;
  onSubmit?: () => void;
}) {
  const { t } = useI18n();
  const message = (field: "name" | "yaml") =>
    problem !== null && problem.field === field ? t(`target.err_${field === "name" ? "name" : "body"}_${problem.code}`) : null;

  return (
    <>
      <FormSection title={t("target.name")} blurb={t("target.name_hint")}>
        <FormField label={t("target.name")} required htmlFor="name" error={message("name")}>
          <input
            id="name"
            name="name"
            value={name}
            readOnly={mode === "edit"}
            onChange={(event) => onName?.(event.target.value)}
            className="w-72 rounded-sm border border-border bg-background px-2 py-1 font-mono text-sm read-only:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </FormField>
        {name !== "" && (
          <p className="pt-2 font-mono text-[11px] text-muted-foreground">
            configs/{targetFilename(name)}
          </p>
        )}
      </FormSection>

      <FormSection title={t("target.yaml")} blurb={t("target.yaml_hint")}>
        <FormField label={t("target.yaml")} required htmlFor="yaml" error={message("yaml")}>
          <textarea
            id="yaml"
            name="yaml"
            value={yaml}
            rows={18}
            spellCheck={false}
            onChange={(event) => onYaml?.(event.target.value)}
            className="w-full rounded-sm border border-border bg-background p-2 font-mono text-xs leading-relaxed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </FormField>
      </FormSection>

      {failure !== undefined && failure !== null && (
        <p role="alert" className="pt-2 text-sm text-status-critical">
          {failure}
        </p>
      )}

      <ActionBar>
        <a
          href={mode === "edit" ? targetHref(name) : targetsHref}
          className="rounded-sm border border-border px-3 py-1.5 text-sm hover:bg-muted"
        >
          {t("common.cancel")}
        </a>
        <button
          type="button"
          onClick={onSubmit}
          disabled={saving === true}
          className="rounded-sm bg-foreground px-3 py-1.5 text-sm font-medium text-background hover:bg-foreground/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {saving === true ? t("target.saving") : t("target.save")}
        </button>
      </ActionBar>
    </>
  );
}

export function TargetForm({ name: editing }: { name?: string }) {
  const { t } = useI18n();
  const mode: "new" | "edit" = editing === undefined ? "new" : "edit";
  const existing = useJSON<TargetDetailData>(
    editing === undefined ? "api/meta" : `api/targets/${encodeURIComponent(editing)}`,
  );
  const [name, setName] = useState(editing ?? "");
  const [yaml, setYaml] = useState(mode === "new" ? newTargetTemplate() : "");
  const [loaded, setLoaded] = useState(mode === "new");
  const [problem, setProblem] = useState<FormProblem | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // The fetched text seeds the editor once. Re-seeding on every render would
  // throw away what the reader has typed each time anything else changes.
  if (!loaded && mode === "edit" && existing.data !== null) {
    setLoaded(true);
    setYaml(existing.data.yaml ?? "");
  }

  const submit = () => {
    const found = formProblem(name, yaml);
    setProblem(found);
    setFailure(null);
    if (found !== null) return;
    setSaving(true);
    // Editing is by definition an overwrite; creating is not, and the server
    // answers 409 if the name is already taken — which is the collision the
    // reader needs to see rather than have resolved for them.
    putTarget(name, yaml, mode === "edit")
      .then(() => {
        window.location.hash = targetHref(name);
      })
      .catch((err: unknown) => {
        setFailure(err instanceof Error ? err.message : String(err));
        setSaving(false);
      });
  };

  return (
    <>
      <Crumbs
        items={[
          { label: t("target.title"), href: targetsHref },
          ...(mode === "edit" ? [{ label: name, href: targetHref(name) }] : []),
          { label: mode === "edit" ? t("target.form_edit_title") : t("target.form_new_title") },
        ]}
      />
      <PageHeader
        title={mode === "edit" ? t("target.form_edit_title") : t("target.form_new_title")}
        subtitle={t("target.blurb")}
      />
      <TargetFormBody
        mode={mode}
        name={name}
        yaml={yaml}
        problem={problem}
        failure={failure ?? existing.error}
        saving={saving}
        onName={setName}
        onYaml={setYaml}
        onSubmit={submit}
      />
    </>
  );
}
