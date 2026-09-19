/**
 * Config: the vocabulary and the machine.
 *
 * Three read-only faces, none of which the console may edit. Metric meanings
 * and plugin wiring live in code, and a page that let you "change" them here
 * would be a page that lies.
 *
 * The third one is the odd one: a table of words this project has retired.
 * DataBench keeps one and it is the cheapest documentation either project has
 * — someone reading an old runbook, an old commit or an old screenshot meets a
 * dead word and needs one place that says what it became. "Deleted outright"
 * is a real answer and the hardest to find by searching, which is exactly why
 * it belongs in a table rather than in a changelog.
 */

import { useJSON, type PluginInventory } from "@/api";
import { useI18n } from "@/i18n";
import { METRIC_SPECS } from "@/lib/glossary";

/** What a word became. `to` empty = it was removed, not renamed. */
interface RetiredTerm {
  from: string;
  to: string;
  note: { en: string; zh: string };
}

const RETIRED: RetiredTerm[] = [
  {
    from: "COLLECT",
    to: "SEAL",
    note: {
      en: "The lifecycle stage that freezes the evidence. Renamed for what it does.",
      zh: "封存证据的那个生命周期阶段。按它真正做的事改了名。",
    },
  },
  {
    from: "RESOLVE",
    to: "",
    note: {
      en: "A stage that always did the same thing; folded into the phases around it.",
      zh: "一个永远在做同一件事的阶段；并进了它前后的阶段。",
    },
  },
  {
    from: "Task / SuiteTask",
    to: "BenchmarkSuite",
    note: {
      en: "One rail instead of three. A benchmark is the public unit, spelled suite:<id>.",
      zh: "三条轨并成一条。基准是对外的单位，写作 suite:<id>。",
    },
  },
  {
    from: "T1.1 / T4.2 …",
    to: "",
    note: {
      en: "The numbered task codes are gone. Benchmarks have names.",
      zh: "编号任务码已经删掉了。基准有名字。",
    },
  },
  {
    from: "report",
    to: "",
    note: {
      en: "The HTML report subsystem was deleted. Use this viewer, or csbench query / export.",
      zh: "HTML 报告子系统已删除。用这个查看器，或者 csbench query / export。",
    },
  },
  {
    from: "#/record/:id",
    to: "#/runs/:id",
    note: {
      en: "Old links still resolve and rewrite themselves in the address bar.",
      zh: "老链接仍然可达，并会在地址栏里自己改写成新写法。",
    },
  },
  {
    from: "#/live",
    to: "#/observe",
    note: { en: "Same page, current spelling.", zh: "同一个页面，现在的写法。" },
  },
];

export function RetiredVocabulary() {
  const { t, locale } = useI18n();
  return (
    <section className="border-t border-border py-4">
      <h2 className="text-sm font-semibold">{t("config.retired")}</h2>
      <p className="max-w-3xl pt-1 text-xs text-muted-foreground">{t("config.retired_blurb")}</p>
      <ul className="flex flex-col divide-y divide-border pt-3">
        {RETIRED.map((term) => (
          <li key={term.from} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2.5">
            <span className="font-mono text-sm line-through decoration-muted-foreground/60">
              {term.from}
            </span>
            <span aria-hidden className="text-muted-foreground">
              →
            </span>
            <span className="font-mono text-sm">
              {term.to === "" ? (
                <span className="text-muted-foreground">{t("config.removed")}</span>
              ) : (
                term.to
              )}
            </span>
            <span className="min-w-0 text-xs text-muted-foreground">{term.note[locale]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function MetricGlossary() {
  const { t, locale } = useI18n();
  return (
    <section className="border-t border-border py-4">
      <h2 className="text-sm font-semibold">{t("config.metrics")}</h2>
      <p className="max-w-3xl pt-1 text-xs text-muted-foreground">{t("config.metrics_blurb")}</p>
      <ul className="flex flex-col divide-y divide-border pt-3">
        {METRIC_SPECS.map((spec) => (
          <li key={spec.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2.5">
            <span className="w-72 shrink-0 font-mono text-xs">{spec.key}</span>
            <span className="text-sm">{spec.label[locale]}</span>
            {/* Direction is part of the meaning: without it a reader cannot
                tell a good number from a bad one. */}
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
              {spec.betterIs === "none" ? t("config.no_direction") : t(`config.better_${spec.betterIs}`)}
            </span>
            {spec.blurb !== undefined && (
              <span className="min-w-0 basis-full text-xs text-muted-foreground">
                {spec.blurb[locale]}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function PluginTable({ inventory }: { inventory: PluginInventory }) {
  const { t } = useI18n();
  const kinds = [...new Set(inventory.plugins.map((entry) => entry.kind))];
  return (
    <section className="border-t border-border py-4">
      <h2 className="text-sm font-semibold">{t("config.plugins")}</h2>
      <p className="max-w-3xl pt-1 text-xs text-muted-foreground">{t("config.plugins_blurb")}</p>
      <p className="pt-2 font-mono text-xs text-muted-foreground">
        csbench {inventory.core_version} · plugin API {inventory.plugin_api}
      </p>
      {kinds.map((kind) => (
        <div key={kind} className="pt-4">
          <h3 className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">
            {kind}
          </h3>
          <ul className="flex flex-col divide-y divide-border pt-1">
            {inventory.plugins
              .filter((entry) => entry.kind === kind)
              .map((entry) => (
                <li
                  key={`${entry.kind}:${entry.name}`}
                  className="flex flex-wrap items-baseline gap-x-3 py-2"
                >
                  <span className="w-56 shrink-0 font-mono text-sm">{entry.name}</span>
                  <span className="min-w-0 font-mono text-xs text-muted-foreground">
                    {entry.target}
                  </span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {entry.distribution === "" ? "—" : entry.distribution}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

export function ConfigView() {
  const { t } = useI18n();
  const inventory = useJSON<PluginInventory>("api/plugins");
  return (
    <>
      <MetricGlossary />
      {inventory.data !== null && <PluginTable inventory={inventory.data} />}
      {inventory.error !== null && (
        <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
          {inventory.error}
        </p>
      )}
      <RetiredVocabulary />
      <p className="py-4 text-xs text-muted-foreground">{t("config.readonly_notice")}</p>
    </>
  );
}
