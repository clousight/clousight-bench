/**
 * The cloud platforms this build can measure.
 *
 * This is the catalogue answer to "what can we point a benchmark at" — the
 * managed products themselves (Aliyun AgentRun, AWS Bedrock AgentCore, Huawei
 * AgentArts, Volcengine AgentKit, a JDBC endpoint, a managed LLM), grouped by
 * the domain they serve. Every row is read from the installed adapter, not
 * from a list kept beside it, so the page cannot claim a platform the build
 * cannot actually reach.
 *
 * **Maturity is the column that matters.** `reference` is an offline
 * stand-in, `experimental` reaches a real service, `skeleton` is wired but
 * unproven against the vendor. Without that, a skeleton's empty run count
 * reads as "nobody got round to it" rather than "this cannot run yet" — and
 * the second is the sentence the reader needs before planning a campaign.
 */

import { useJSON } from "@/api";
import { Glossed } from "@/components/Glossed";
import { EmptyState } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

export interface PlatformRow {
  platform: string;
  summary: string;
  status: string;
  docs: string;
  /** null when the server could not count; rendered "—", never 0. */
  runs: number | null;
}

export interface PlatformDomain {
  domain: string;
  description: string;
  platforms: PlatformRow[];
}

export interface PlatformCatalogue {
  domains: PlatformDomain[];
  counted: boolean;
  error?: string;
}

/**
 * Maturity, as colour AND word.
 *
 * `skeleton` deliberately gets the warning tone rather than a neutral one: it
 * is the row a reader is most likely to misread as usable.
 */
const STATUS_TONES: Record<string, string> = {
  reference: "bg-muted text-muted-foreground",
  experimental: "bg-status-good/12 text-status-good",
  skeleton: "bg-status-warning/15 text-status-serious",
  unknown: "bg-muted text-muted-foreground",
};

export function StatusChip({ status }: { status: string }) {
  const { t } = useI18n();
  const known = Object.hasOwn(STATUS_TONES, status);
  return (
    <Glossed blurb={known ? t(`platform.status.${status}_blurb`) : null}>
      <span
        className={cn(
          "inline-flex items-center rounded-sm px-1.5 py-0 font-mono text-[10px] uppercase tracking-[0.08em]",
          STATUS_TONES[status] ?? STATUS_TONES.unknown,
        )}
      >
        {known ? t(`platform.status.${status}`) : status}
      </span>
    </Glossed>
  );
}

/** The domain's name in the reader's language, or null when we have none. */
function domainName(t: (key: string) => string, domain: string): string | null {
  const key = `domain.${domain}`;
  const label = t(key);
  return label === key ? null : label;
}

/** The catalogue, already fetched — split out so it can be tested directly. */
export function PlatformTable({ data }: { data: PlatformCatalogue }) {
  const { t } = useI18n();
  const total = data.domains.reduce((sum, domain) => sum + domain.platforms.length, 0);

  if (data.error !== undefined && data.error !== "") {
    return (
      <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
        {data.error}
      </p>
    );
  }
  if (total === 0) {
    return <EmptyState title={t("platform.none")} blurb={t("platform.none_blurb")} />;
  }

  return (
    <div className="flex flex-col">
      {data.domains.map((domain) => {
        // Domains arrive from plugins, so this glossary cannot know them all.
        // `t` answers with the key it was handed when it has no entry, which
        // would set a heading reading "domain.vector-db" — so an unnamed
        // domain introduces itself by its own name, and drops the code beside
        // it rather than printing the same string twice.
        const named = domainName(t, domain.domain);
        return (
          <section key={domain.domain} className="border-t border-border py-4">
            <div className="flex flex-wrap items-baseline gap-x-3 pb-3">
              <h2 className="text-sm font-semibold">{named ?? domain.domain}</h2>
              {named !== null && (
                <span className="font-mono text-[11px] text-muted-foreground">{domain.domain}</span>
              )}
              <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
                {domain.platforms.length}
              </span>
            </div>
            {domain.description !== "" && (
              <p className="max-w-3xl pb-3 text-xs text-muted-foreground">{domain.description}</p>
            )}
            <ul className="flex flex-col divide-y divide-border">
              {domain.platforms.map((row) => (
                <li
                  key={row.platform}
                  className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2.5"
                >
                  <span className="font-mono text-sm">{row.platform}</span>
                  <StatusChip status={row.status} />
                  {row.summary !== "" && (
                    <span className="min-w-0 text-sm text-muted-foreground">{row.summary}</span>
                  )}
                  <span className="ml-auto flex items-baseline gap-3">
                    {row.docs !== "" && (
                      <a
                        href={row.docs}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                      >
                        {t("platform.docs")}
                      </a>
                    )}
                    <span className="font-mono text-xs tabular-nums text-muted-foreground">
                      {row.runs === null ? "—" : row.runs}
                      <span className="ml-1">{t("platform.runs")}</span>
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

export function PlatformsView() {
  const { t } = useI18n();
  const catalogue = useJSON<PlatformCatalogue>("api/platforms");
  if (catalogue.error !== null) {
    return (
      <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
        {catalogue.error}
      </p>
    );
  }
  if (catalogue.data === null) {
    return (
      <p className="border-t border-border py-6 text-sm text-muted-foreground">
        {t("common.loading")}
      </p>
    );
  }
  return <PlatformTable data={catalogue.data} />;
}
