/**
 * Everything the conclusion view leaves out.
 *
 * `fingerprints`, `identity` and `environment` are the fields that make a run
 * reproducible and comparable — and the previous viewer simply never rendered
 * them, so the most auditable parts of a record were invisible. They are here
 * in full, behind the engineer-view switch, because showing them by default is
 * what made the page unreadable in the first place.
 */

import type { RecordDetailData } from "@/api";
import { CopyButton } from "@/components/CopyButton";
import { Field } from "@/components/Glossed";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n";
import { useEngineerView } from "@/lib/engineerView";

export function EngineerPanel({ data }: { data: RecordDetailData }) {
  const { t } = useI18n();
  const { engineerView } = useEngineerView();
  if (!engineerView) return null;

  const fingerprints = data.fingerprints ?? {};
  const environment = data.environment ?? {};
  const identity = data.identity ?? {};
  const extensions = data.extensions ?? {};

  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle>{t("engineer.title")}</CardTitle>
        <CardDescription>{t("engineer.blurb")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <Section title={t("engineer.fingerprints")} blurb={t("engineer.fingerprints_blurb")}>
          {Object.entries(fingerprints).map(([name, value]) => (
            <Field key={name} label={name}>
              <span className="break-all font-mono text-[11px]">{String(value)}</span>
              <CopyButton value={String(value)} />
            </Field>
          ))}
        </Section>

        <Section title={t("engineer.identity")} blurb={t("engineer.identity_blurb")}>
          {Object.entries(identity).map(([name, value]) => (
            <Field key={name} label={name}>
              <span className="break-all font-mono text-[11px]">
                {typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}
              </span>
            </Field>
          ))}
        </Section>

        <Section title={t("engineer.environment")} blurb={t("engineer.environment_blurb")}>
          {Object.entries(environment).map(([name, value]) => (
            <Field key={name} label={name}>
              <span className="break-all font-mono text-[11px]">
                {typeof value === "object" && value !== null ? JSON.stringify(value) : String(value)}
              </span>
            </Field>
          ))}
        </Section>

        {Object.keys(extensions).length > 0 && (
          <Section title={t("engineer.extensions")} blurb={t("engineer.extensions_blurb")}>
            <pre className="overflow-x-auto rounded-md border bg-muted/30 p-2 font-mono text-[11px]">
              {JSON.stringify(extensions, null, 2)}
            </pre>
          </Section>
        )}
      </CardContent>
    </Card>
  );
}

function Section({
  title,
  blurb,
  children,
}: {
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  const hasContent = Array.isArray(children) ? children.length > 0 : children !== null;
  if (!hasContent) return null;
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mb-1 text-xs text-muted-foreground">{blurb}</p>
      <dl className="divide-y">{children}</dl>
    </div>
  );
}
