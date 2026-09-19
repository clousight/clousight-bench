/**
 * Start a run.
 *
 * One scrolling form with sections and a sticky action bar, not a wizard: the
 * whole request is six fields, and a stepper would hide three of them behind a
 * "next" button for no gain.
 *
 * The confirmation step is the part worth arguing about. It exists to show
 * what the run will FREEZE that nobody typed — today that is the benchmark's
 * pinned data version — and to say out loud when the platform will reach a
 * real service and bill for it. It deliberately does not print an evaluator id
 * or a dataset digest: both are resolved by the run itself, and a guess here
 * would look exactly like provenance while being invented.
 */

import { useState } from "react";

import {
  startRun,
  useJSON,
  type LaunchOptions,
  type TargetListData,
  type TargetSummary,
} from "@/api";
import { ActionBar, Crumbs, FormField, FormSection, PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import {
  initialChoice,
  launchWarnings,
  paramsFromPairs,
  type LaunchChoice,
  type ParamPair,
} from "@/lib/launch";
import { observeRunHref, runsHref } from "@/router";

const inputClass =
  "rounded-sm border border-border bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function RunNewForm({
  options,
  targets,
  choice,
  target,
  params,
  repeat,
  warmup,
  onChoice,
  onTarget,
  onParams,
  onRepeat,
  onWarmup,
}: {
  options: LaunchOptions;
  targets: TargetSummary[];
  choice: LaunchChoice;
  target: string | null;
  params: ParamPair[];
  repeat: number;
  warmup: number;
  onChoice?: (next: LaunchChoice) => void;
  onTarget?: (next: string | null) => void;
  onParams?: (next: ParamPair[]) => void;
  onRepeat?: (next: number) => void;
  onWarmup?: (next: number) => void;
}) {
  const { t } = useI18n();
  const domain = options.domains.find((entry) => entry.domain === choice.domain);
  const rows = params.length > 0 ? params : [{ key: "", value: "" }];

  return (
    <>
      <FormSection title={t("run.section_what")} blurb={t("run.section_what_blurb")}>
        <div className="grid gap-4 sm:grid-cols-3">
          <FormField label={t("run.benchmark")} required htmlFor="suite" hint={t("run.benchmark_hint")}>
            <select
              id="suite"
              value={choice.suiteId}
              onChange={(event) => onChoice?.({ ...choice, suiteId: event.target.value })}
              className={inputClass}
            >
              {options.suites.map((suite) => (
                <option key={suite.suite_id} value={suite.suite_id}>
                  {suite.suite_id}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label={t("run.domain")} required htmlFor="domain" hint={t("run.domain_hint")}>
            <select
              id="domain"
              value={choice.domain}
              onChange={(event) => {
                const next = options.domains.find((entry) => entry.domain === event.target.value);
                onChoice?.({
                  ...choice,
                  domain: event.target.value,
                  // The platform list is the domain's; keeping the old one
                  // would submit a pairing the server refuses by definition.
                  platform: next?.platforms[0]?.platform ?? "",
                });
              }}
              className={inputClass}
            >
              {options.domains.map((entry) => (
                <option key={entry.domain} value={entry.domain}>
                  {entry.domain}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label={t("run.platform")} required htmlFor="platform" hint={t("run.platform_hint")}>
            <select
              id="platform"
              value={choice.platform}
              onChange={(event) => onChoice?.({ ...choice, platform: event.target.value })}
              className={inputClass}
            >
              {(domain?.platforms ?? []).map((entry) => (
                <option key={entry.platform} value={entry.platform}>
                  {entry.platform} · {entry.status}
                </option>
              ))}
            </select>
          </FormField>
        </div>
        <div className="grid gap-4 pt-4 sm:grid-cols-3">
          <FormField label={t("run.target")} htmlFor="target" hint={t("run.target_hint")}>
            <select
              id="target"
              value={target ?? ""}
              onChange={(event) => onTarget?.(event.target.value === "" ? null : event.target.value)}
              className={inputClass}
            >
              <option value="">{t("run.no_target")}</option>
              {targets.map((entry) => (
                <option key={entry.name} value={entry.name}>
                  {entry.name}
                </option>
              ))}
            </select>
          </FormField>
        </div>
      </FormSection>

      <FormSection title={t("run.section_params")} blurb={t("run.section_params_blurb")}>
        <div className="flex flex-col gap-2">
          {rows.map((row, index) => (
            <div key={index} className="flex items-center gap-2">
              <input
                aria-label={t("run.param_key")}
                value={row.key}
                placeholder={t("run.param_key")}
                onChange={(event) => {
                  const next = [...rows];
                  next[index] = { ...row, key: event.target.value };
                  onParams?.(next);
                }}
                className={`${inputClass} w-56 font-mono`}
              />
              <input
                aria-label={t("run.param_value")}
                value={row.value}
                placeholder={t("run.param_value")}
                onChange={(event) => {
                  const next = [...rows];
                  next[index] = { ...row, value: event.target.value };
                  onParams?.(next);
                }}
                className={`${inputClass} w-56 font-mono`}
              />
            </div>
          ))}
          <button
            type="button"
            onClick={() => onParams?.([...rows, { key: "", value: "" }])}
            className="self-start text-xs text-muted-foreground underline underline-offset-4"
          >
            {t("run.param_add")}
          </button>
        </div>
      </FormSection>

      <FormSection title={t("run.section_exec")} blurb={t("run.section_exec_blurb")}>
        <div className="grid gap-4 sm:grid-cols-3">
          <FormField
            label={t("run.repeat")}
            htmlFor="repeat"
            hint={t("run.repeat_hint").replace("{max}", String(options.max_repeat))}
          >
            <input
              id="repeat"
              type="number"
              min={1}
              max={options.max_repeat}
              value={repeat}
              onChange={(event) => onRepeat?.(Number(event.target.value))}
              className={`${inputClass} w-24 font-mono tabular-nums`}
            />
          </FormField>
          <FormField
            label={t("run.warmup")}
            htmlFor="warmup"
            hint={t("run.warmup_hint").replace("{max}", String(options.max_warmup))}
          >
            <input
              id="warmup"
              type="number"
              min={0}
              max={options.max_warmup}
              value={warmup}
              onChange={(event) => onWarmup?.(Number(event.target.value))}
              className={`${inputClass} w-24 font-mono tabular-nums`}
            />
          </FormField>
        </div>
      </FormSection>
    </>
  );
}

export function RunConfirm({
  options,
  choice,
  target,
  params,
  repeat,
  warmup,
}: {
  options: LaunchOptions;
  choice: LaunchChoice;
  target: string | null;
  params: Record<string, string | number | boolean>;
  repeat: number;
  warmup: number;
}) {
  const { t } = useI18n();
  const suite = options.suites.find((entry) => entry.suite_id === choice.suiteId);
  const warnings = launchWarnings(choice, options);
  const paramKeys = Object.keys(params);

  return (
    <div className="flex flex-col gap-3 border-t border-border pt-4">
      <h2 className="text-sm font-semibold">{t("run.confirm_title")}</h2>
      <p className="max-w-3xl text-xs text-muted-foreground">{t("run.confirm_blurb")}</p>
      <dl className="flex flex-wrap gap-x-8 gap-y-3">
        {[
          { label: t("run.benchmark"), value: choice.suiteId },
          { label: t("run.pinned_data"), value: suite?.suite_version ?? "—", mono: true },
          { label: t("run.platform"), value: choice.platform },
          { label: t("run.target"), value: target ?? t("run.no_target") },
          { label: t("run.repeat"), value: String(repeat) },
          { label: t("run.warmup"), value: String(warmup) },
          {
            label: t("run.params_frozen"),
            value: paramKeys.length === 0 ? t("run.params_none") : paramKeys.join(", "),
          },
        ].map((fact) => (
          <div key={fact.label} className="flex min-w-0 flex-col gap-0.5">
            <dt className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
              {fact.label}
            </dt>
            <dd className={fact.mono === true ? "font-mono text-xs" : "text-sm"}>{fact.value}</dd>
          </div>
        ))}
      </dl>
      {warnings.map((warning) => (
        <p
          key={warning}
          role="alert"
          className={
            warning === "cost"
              ? "text-sm text-status-serious"
              : "text-sm text-muted-foreground"
          }
        >
          {t(`run.warn_${warning}`)}
        </p>
      ))}
    </div>
  );
}

/**
 * The form, or the review — never both.
 *
 * Appending the review under the form put it below a sticky action bar and off
 * the bottom of the page: the reader pressed "review", watched the button
 * change, and never saw what appeared. Two steps, one visible at a time.
 */
export function RunNewBody({
  confirming,
  options,
  targets,
  choice,
  target,
  params,
  repeat,
  warmup,
  onChoice,
  onTarget,
  onParams,
  onRepeat,
  onWarmup,
}: {
  confirming: boolean;
  options: LaunchOptions;
  targets: TargetSummary[];
  choice: LaunchChoice;
  target: string | null;
  params: ParamPair[];
  repeat: number;
  warmup: number;
  onChoice?: (next: LaunchChoice) => void;
  onTarget?: (next: string | null) => void;
  onParams?: (next: ParamPair[]) => void;
  onRepeat?: (next: number) => void;
  onWarmup?: (next: number) => void;
}) {
  if (confirming) {
    return (
      <RunConfirm
        options={options}
        choice={choice}
        target={target}
        params={paramsFromPairs(params)}
        repeat={repeat}
        warmup={warmup}
      />
    );
  }
  return (
    <RunNewForm
      options={options}
      targets={targets}
      choice={choice}
      target={target}
      params={params}
      repeat={repeat}
      warmup={warmup}
      onChoice={onChoice}
      onTarget={onTarget}
      onParams={onParams}
      onRepeat={onRepeat}
      onWarmup={onWarmup}
    />
  );
}

export function RunNewView() {
  const { t } = useI18n();
  const options = useJSON<LaunchOptions>("api/runs/options");
  const targetList = useJSON<TargetListData>("api/targets");
  const [choice, setChoice] = useState<LaunchChoice | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [params, setParams] = useState<ParamPair[]>([{ key: "", value: "" }]);
  const [repeat, setRepeat] = useState(1);
  const [warmup, setWarmup] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  if (options.error !== null) {
    return (
      <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
        {options.error}
      </p>
    );
  }
  if (options.data === null) {
    return <p className="border-t border-border py-6 text-sm text-muted-foreground">{t("common.loading")}</p>;
  }

  // Chosen once the catalogue has arrived; see initialChoice for why it is
  // not simply the first of each list.
  const current: LaunchChoice = choice ?? initialChoice(options.data);

  const start = () => {
    setStarting(true);
    setFailure(null);
    startRun({
      domain: current.domain,
      task_id: `suite:${current.suiteId}`,
      platform: current.platform,
      target,
      params: paramsFromPairs(params),
      repeat,
      warmup,
    })
      .then((runId) => {
        // No new live machinery: `csbench run` writes the progress plane and
        // the observe view already consumes it.
        window.location.hash = observeRunHref(runId);
      })
      .catch((err: unknown) => {
        setFailure(err instanceof Error ? err.message : String(err));
        setStarting(false);
      });
  };

  return (
    <>
      <Crumbs items={[{ label: t("runs.title"), href: runsHref }, { label: t("run.new_title") }]} />
      <PageHeader title={t("run.new_title")} subtitle={t("run.new_blurb")} />
      <RunNewBody
        confirming={confirming}
        options={options.data}
        targets={targetList.data?.targets ?? []}
        choice={current}
        target={target}
        params={params}
        repeat={repeat}
        warmup={warmup}
        onChoice={setChoice}
        onTarget={setTarget}
        onParams={setParams}
        onRepeat={setRepeat}
        onWarmup={setWarmup}
      />
      {failure !== null && (
        <p role="alert" className="pt-3 text-sm text-status-critical">
          {failure}
        </p>
      )}
      <ActionBar>
        {confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(false)}
            className="rounded-sm border border-border px-3 py-1.5 text-sm hover:bg-muted"
          >
            {t("run.back_to_form")}
          </button>
        ) : (
          <a href={runsHref} className="rounded-sm border border-border px-3 py-1.5 text-sm hover:bg-muted">
            {t("common.cancel")}
          </a>
        )}
        {!confirming ? (
          <button
            type="button"
            onClick={() => setConfirming(true)}
            className="rounded-sm bg-foreground px-3 py-1.5 text-sm font-medium text-background hover:bg-foreground/90"
          >
            {t("run.review")}
          </button>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={starting}
            className="rounded-sm bg-foreground px-3 py-1.5 text-sm font-medium text-background hover:bg-foreground/90 disabled:opacity-50"
          >
            {starting ? t("run.starting") : t("run.start")}
          </button>
        )}
      </ActionBar>
    </>
  );
}
