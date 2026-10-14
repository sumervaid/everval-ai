import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Beaker,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Download,
  LockKeyhole,
  Play,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Square,
  XCircle,
} from 'lucide-react';
import { TrialReviews } from './Reviews';
import { ScheduleForm } from './Schedules';
import { SubmissionImport } from './SubmissionImport';
import { downloadReport, post, request } from '../api';
import type { Experiment, Report, System, Trial, Workspace } from '../types';
import { date, duration, errorMessage, isActive, money, percent } from '../format';
import {
  Button,
  DomainBadge,
  EmptyState,
  Field,
  JsonView,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Status,
} from '../components';

export function ExperimentTable({
  experiments,
  emptyAction,
}: {
  experiments: Experiment[];
  emptyAction?: ReactNode;
}) {
  if (!experiments.length)
    return (
      <EmptyState
        compact
        title="Your first result starts here."
        description="Choose a collection and the systems you want to compare. Every trial will be recorded."
        action={emptyAction}
      />
    );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Experiment</th>
            <th>Status</th>
            <th>Trials</th>
            <th>Created</th>
            <th>
              <span className="visually-hidden">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {experiments.map((e) => (
            <tr key={e.id}>
              <td>
                <a className="table-primary" href={`#/experiments/${encodeURIComponent(e.id)}`}>
                  {e.name}
                </a>
                <span className="table-secondary">
                  {e.system_ids.length} systems · {e.repetitions}{' '}
                  {e.repetitions === 1 ? 'repetition' : 'repetitions'}
                </span>
              </td>
              <td>
                <Status value={e.status} />
              </td>
              <td>
                <span className="tabular">
                  {e.completed_trials} <span className="text-muted">/ {e.total_trials}</span>
                </span>
              </td>
              <td className="text-muted">{date(e.created_at)}</td>
              <td>
                <a
                  className="icon-button"
                  aria-label={`Open ${e.name}`}
                  href={`#/experiments/${encodeURIComponent(e.id)}`}
                >
                  <ArrowRight size={16} />
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function ExperimentsPage({
  workspace,
  refresh,
}: {
  workspace: Workspace;
  refresh: () => Promise<void>;
}) {
  const [importing, setImporting] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const experiments = workspace.experiments.filter(
    (e) =>
      e.name.toLowerCase().includes(query.toLowerCase()) &&
      (status === 'all' || e.status === status),
  );
  return (
    <>
      <PageHeader
        eyebrow="CONTROL THE COMPARISON"
        title="Experiments"
        description="Fixed task cohorts, frozen configurations, and a complete record of every attempt."
        actions={
          <>
            <Button variant="secondary" onClick={() => setImporting(true)}>
              <Download size={16} />
              Import results
            </Button>
            <LinkButton href="#/experiments/new">
              <Plus size={16} />
              New experiment
            </LinkButton>
          </>
        }
      />
      {importing && <SubmissionImport refresh={refresh} onClose={() => setImporting(false)} />}
      <div className="filter-bar">
        <label className="search-field">
          <Search size={17} />
          <input
            aria-label="Search experiments"
            placeholder="Search experiments…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="Filter experiment status"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="all">All statuses</option>
          {['queued', 'running', 'completed', 'cancelled', 'failed'].map((s) => (
            <option key={s} value={s}>
              {s[0].toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
        <span className="result-count">{experiments.length} experiments</span>
      </div>
      <Panel>
        <ExperimentTable
          experiments={experiments}
          emptyAction={
            <LinkButton href="#/experiments/new" secondary>
              <Plus size={15} />
              Create experiment
            </LinkButton>
          }
        />
      </Panel>
      <div className="principles-strip">
        <LockKeyhole size={18} />
        <strong>Every run is a snapshot.</strong>
        <span>Tasks and system settings are preserved with the experiment.</span>
      </div>
    </>
  );
}
export function NewExperiment({
  workspace,
  refresh,
}: {
  workspace: Workspace;
  refresh: () => Promise<void>;
}) {
  const selectedCollection = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get(
    'collection',
  );
  const [collectionId, setCollectionId] = useState(
    selectedCollection ?? workspace.collections[0]?.id ?? '',
  );
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [repetitions, setRepetitions] = useState(1);
  const [budget, setBudget] = useState('0');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const collection = workspace.collections.find((c) => c.id === collectionId);
  const selectedSystems = workspace.systems.filter((s) => selected.includes(s.id));
  const fixturesOnly =
    selectedSystems.length > 0 &&
    selectedSystems.every((s) => s.kind === 'reference' || s.kind === 'incomplete');
  const trials = (collection?.scenarios.length ?? 0) * selected.length * repetitions;
  return (
    <>
      <a className="back-link" href="#/experiments">
        <ArrowLeft size={15} />
        All experiments
      </a>
      <PageHeader
        eyebrow="DESIGN A FAIR TEST"
        title="New experiment"
        description="Evaluate systems on the same scenarios, with an explicit repetition count and budget."
      />
      {!workspace.collections.length || !workspace.systems.some((s) => s.kind !== 'replay') ? (
        <Panel>
          <EmptyState
            title={
              !workspace.collections.length
                ? 'Add a collection first.'
                : 'Configure a system first.'
            }
            description="An experiment needs at least one scenario collection and one system. Starter collections include fixture controls for an offline first run."
            action={
              <LinkButton href={!workspace.collections.length ? '#/collections' : '#/systems'}>
                {!workspace.collections.length ? 'Open collections' : 'Configure systems'}
                <ArrowRight size={16} />
              </LinkButton>
            }
          />
        </Panel>
      ) : (
        <form
          className="authoring-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            try {
              if (!selected.length) throw new Error('Choose at least one system.');
              const experiment = await post<Experiment>('/experiments', {
                name: name.trim() || `${collection?.name} comparison`,
                collection_id: collectionId,
                system_ids: selected,
                repetitions,
                budget_usd: Number(budget),
              });
              await refresh();
              window.location.hash = `/experiments/${encodeURIComponent(experiment.id)}`;
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="authoring-main">
            {error && <Notice tone="error">{error}</Notice>}
            <Panel
              title="1. Choose the work"
              detail="The experiment preserves this collection’s current version."
            >
              <div className="panel-body">
                <Field label="Experiment name">
                  <input
                    maxLength={160}
                    placeholder={
                      collection ? `${collection.name} comparison` : 'Give this experiment a name'
                    }
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <Field label="Collection">
                  <select value={collectionId} onChange={(e) => setCollectionId(e.target.value)}>
                    {workspace.collections.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} · {c.scenarios.length} scenarios
                      </option>
                    ))}
                  </select>
                </Field>
                {collection && (
                  <div className="selected-collection">
                    <DomainBadge domain={collection.domain} />
                    <span>{collection.scenarios.length} scenarios</span>
                    <span>Version {collection.version}</span>
                    <a href={`#/collections/${encodeURIComponent(collection.id)}`}>
                      Review collection
                      <ArrowRight size={13} />
                    </a>
                  </div>
                )}
              </div>
            </Panel>
            <Panel title="2. Select systems" detail="Each selected system attempts every scenario.">
              <div className="system-picker">
                {workspace.systems
                  .filter((s) => s.kind !== 'replay')
                  .map((s) => (
                    <label
                      key={s.id}
                      className={`system-choice ${selected.includes(s.id) ? 'selected' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={selected.includes(s.id)}
                        onChange={(e) =>
                          setSelected(
                            e.target.checked
                              ? [...selected, s.id]
                              : selected.filter((id) => id !== s.id),
                          )
                        }
                      />
                      <div>
                        <strong>{s.name}</strong>
                        <span>
                          {s.kind === 'http'
                            ? s.model
                            : s.kind === 'reference'
                              ? 'Reference output replay'
                              : 'Incomplete answer control'}
                        </span>
                      </div>
                      <span className={`pill ${s.kind !== 'http' ? 'amber' : ''}`}>
                        {s.kind === 'http' ? 'Model' : 'Fixture control'}
                      </span>
                    </label>
                  ))}
              </div>
            </Panel>
            <Panel
              title="3. Set the protocol"
              detail="Keep repetition counts and constraints comparable."
            >
              <div className="panel-body">
                <div className="form-grid">
                  <Field
                    label="Repetitions per scenario"
                    hint="Repeated trials help assess consistency."
                  >
                    <input
                      required
                      type="number"
                      min="1"
                      max="30"
                      value={repetitions}
                      onChange={(e) => setRepetitions(Number(e.target.value))}
                    />
                  </Field>
                  <Field
                    label="Total provider budget · USD"
                    hint="Required for remote providers. Fixture controls incur no model spend."
                  >
                    <input
                      required
                      type="number"
                      min="0"
                      step="0.01"
                      value={budget}
                      onChange={(e) => setBudget(e.target.value)}
                    />
                  </Field>
                </div>
                <Notice>
                  Missing usage stays unknown. Failed, cancelled, and incomplete trials remain
                  visible in the report.
                </Notice>
              </div>
            </Panel>
            <div className="form-footer">
              <LinkButton href="#/experiments" secondary>
                Cancel
              </LinkButton>
              <Button type="submit" busy={busy} disabled={!selected.length || !collectionId}>
                <Play size={16} />
                Start experiment
              </Button>
            </div>
          </div>
          <aside className="authoring-aside">
            <div className="experiment-summary">
              <div className="eyebrow">EXPERIMENT SNAPSHOT</div>
              <h3>{name || 'Your comparison'}</h3>
              <dl>
                <div>
                  <dt>Scenarios</dt>
                  <dd>{collection?.scenarios.length ?? 0}</dd>
                </div>
                <div>
                  <dt>Systems</dt>
                  <dd>{selected.length}</dd>
                </div>
                <div>
                  <dt>Repetitions</dt>
                  <dd>{repetitions || 0}</dd>
                </div>
              </dl>
              <div className="total-trials">
                <span>Total scheduled trials</span>
                <strong>{trials || 0}</strong>
              </div>
              <div className="summary-budget">
                <span>Provider budget</span>
                <strong>{money(Number(budget))}</strong>
              </div>
              {fixturesOnly && (
                <div className="fixture-summary">
                  <Beaker size={18} />
                  <p>
                    <strong>Fixture-only experiment</strong>This validates the pipeline. It does not
                    measure model performance.
                  </p>
                </div>
              )}
              <p className="small-note">
                <LockKeyhole size={14} />
                Collection and system configurations are frozen when the experiment is created.
              </p>
            </div>
          </aside>
        </form>
      )}
    </>
  );
}
export function ExperimentDetail({ id, refresh }: { id: string; refresh: () => Promise<void> }) {
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [tab, setTab] = useState<'results' | 'trials' | 'snapshot'>('results');
  const [trialFilter, setTrialFilter] = useState('all');
  const [systemFilter, setSystemFilter] = useState('all');
  const [expanded, setExpanded] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const e = await request<Experiment>(`/experiments/${encodeURIComponent(id)}`, {
          signal: controller.signal,
        });
        setExperiment(e);
        setError('');
        if (isActive(e.status)) timer = setTimeout(() => void load(), 1500);
      } catch (e) {
        if (!controller.signal.aborted) {
          setError(errorMessage(e));
          timer = setTimeout(() => void load(), 5000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);
  if (!experiment && error)
    return (
      <Notice tone="error">
        {error} <a href="#/experiments">Back to experiments</a>
      </Notice>
    );
  if (!experiment) return <Loading text="Loading experiment" />;
  const e = experiment;
  const active = isActive(e.status);
  const fixtures = e.systems.filter((s) => s.kind === 'reference' || s.kind === 'incomplete');
  const fixturesOnly = fixtures.length === e.systems.length;
  const trials = (e.trials ?? []).filter(
    (t) =>
      (systemFilter === 'all' || t.system_id === systemFilter) &&
      (trialFilter === 'all' ||
        (trialFilter === 'passed' && t.passed === true) ||
        (trialFilter === 'failed' && t.passed === false) ||
        (trialFilter === 'incomplete' && t.passed == null)),
  );
  async function action(kind: 'cancel' | 'rerun') {
    setBusy(true);
    setError('');
    try {
      const updated = await post<Experiment>(`/experiments/${encodeURIComponent(id)}/${kind}`);
      await refresh();
      if (kind === 'rerun') window.location.hash = `/experiments/${encodeURIComponent(updated.id)}`;
      else setExperiment(updated);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <a className="back-link" href="#/experiments">
        <ArrowLeft size={15} />
        All experiments
      </a>
      <PageHeader
        eyebrow="EXPERIMENT RECORD"
        title={e.name}
        description={`Created ${date(e.created_at)} · Collection v${e.collection_version}`}
        actions={
          <>
            {!active && !e.systems.some((s) => s.kind === 'replay') && (
              <Button variant="secondary" onClick={() => setScheduling(!scheduling)}>
                Schedule
              </Button>
            )}
            <div className="export-control">
              <Download size={15} />
              <select
                aria-label="Export report"
                value=""
                disabled={exporting}
                onChange={async (event) => {
                  const format = event.target.value as 'json' | 'csv' | 'html';
                  if (!format) return;
                  setExporting(true);
                  try {
                    await downloadReport(id, format);
                  } catch (err) {
                    setError(errorMessage(err));
                  } finally {
                    setExporting(false);
                  }
                }}
              >
                <option value="">{exporting ? 'Exporting…' : 'Export report'}</option>
                <option value="json">JSON evidence</option>
                <option value="csv">CSV results</option>
                <option value="html">HTML report</option>
              </select>
            </div>
            {active ? (
              <Button variant="secondary" busy={busy} onClick={() => void action('cancel')}>
                <Square size={14} />
                Cancel run
              </Button>
            ) : (
              <Button variant="secondary" busy={busy} onClick={() => void action('rerun')}>
                <RotateCcw size={15} />
                Rerun experiment
              </Button>
            )}
          </>
        }
      />
      {error && <Notice tone="error">{error}</Notice>}
      {scheduling && <ScheduleForm experiment={e} onClose={() => setScheduling(false)} />}
      {!!fixtures.length && (
        <Notice tone="warning">
          <strong>
            {fixturesOnly ? 'Fixture-control results.' : 'Contains fixture controls.'}
          </strong>{' '}
          {fixturesOnly
            ? 'These trials replay reference and incomplete answers to verify the pipeline. They do not measure model performance.'
            : 'Reference and incomplete controls are not models. Interpret their results separately from provider-backed systems.'}
        </Notice>
      )}
      <div className="run-progress">
        <div>
          <Status value={e.status} />
          <span>
            <strong>{e.completed_trials}</strong> / {e.total_trials} trials finalized
          </span>
          <span>
            {e.system_ids.length} systems · {e.repetitions} repetitions
          </span>
        </div>
        <div className="progress-track">
          <span
            style={{
              width: `${e.total_trials ? Math.min(100, (e.completed_trials / e.total_trials) * 100) : 0}%`,
            }}
          />
        </div>
        <span className="small-note">
          {active
            ? 'The local worker is processing this experiment. Evidence updates automatically.'
            : `Finished ${date(e.finished_at)}. Every scheduled trial is retained.`}
        </span>
      </div>
      <div className="tabs" role="tablist" aria-label="Experiment views">
        <button role="tab" aria-selected={tab === 'results'} onClick={() => setTab('results')}>
          Comparison
        </button>
        <button role="tab" aria-selected={tab === 'trials'} onClick={() => setTab('trials')}>
          Trial evidence <span className="tab-count">{e.trials?.length ?? 0}</span>
        </button>
        <button role="tab" aria-selected={tab === 'snapshot'} onClick={() => setTab('snapshot')}>
          Frozen configuration
        </button>
      </div>
      {tab === 'results' &&
        (e.report ? (
          <ReportView report={e.report} systems={e.systems} />
        ) : (
          <Panel>
            <EmptyState
              title={active ? 'Evidence is being collected.' : 'No report is available yet.'}
              description={
                active
                  ? 'Inspect the Trial evidence tab to see individual attempts as they finish.'
                  : 'Inspect individual trials for failures or cancellation details.'
              }
              action={
                <Button variant="secondary" onClick={() => setTab('trials')}>
                  Inspect trial evidence
                  <ArrowRight size={15} />
                </Button>
              }
            />
          </Panel>
        ))}
      {tab === 'trials' && (
        <>
          <div className="filter-bar">
            <select
              aria-label="Filter trials by system"
              value={systemFilter}
              onChange={(event) => setSystemFilter(event.target.value)}
            >
              <option value="all">All systems</option>
              {e.systems.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter trial outcome"
              value={trialFilter}
              onChange={(event) => setTrialFilter(event.target.value)}
            >
              <option value="all">All outcomes</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed checks</option>
              <option value="incomplete">Incomplete / error</option>
            </select>
            <span className="result-count">{trials.length} trials</span>
          </div>
          <Panel>
            {!trials.length ? (
              <EmptyState
                compact
                title="No trials match this view."
                description="Try another filter or wait for the worker to record an attempt."
              />
            ) : (
              <div className="trial-list">
                {trials.map((t) => (
                  <div className={`trial-item ${expanded === t.id ? 'expanded' : ''}`} key={t.id}>
                    <button
                      className="trial-summary"
                      aria-expanded={expanded === t.id}
                      onClick={() => setExpanded(expanded === t.id ? '' : t.id)}
                    >
                      {expanded === t.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      <div className="trial-name">
                        <strong>{t.scenario_name}</strong>
                        <span>
                          {t.system_name} · Repetition {t.repetition}
                        </span>
                      </div>
                      <DomainBadge domain={t.domain} />
                      <Status
                        value={
                          t.passed === true ? 'passed' : t.passed === false ? 'failed' : t.status
                        }
                      />
                      <span className="trial-metric">{duration(t.duration_ms)}</span>
                      <span className="trial-metric">{money(t.cost_usd)}</span>
                    </button>
                    {expanded === t.id && <TrialEvidence trial={t} />}
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}
      {tab === 'snapshot' && (
        <>
          <Panel
            title="Frozen experiment"
            detail="This record identifies the task collection and protocol used in this run."
          >
            <dl className="settings-list">
              <div>
                <dt>Experiment ID</dt>
                <dd>
                  <code>{e.id}</code>
                </dd>
              </div>
              <div>
                <dt>Collection ID</dt>
                <dd>
                  <code>{e.collection_id}</code>
                </dd>
              </div>
              <div>
                <dt>Collection version</dt>
                <dd>{e.collection_version}</dd>
              </div>
              <div>
                <dt>Collection hash</dt>
                <dd>
                  <code className="break-word">{e.collection_hash}</code>
                </dd>
              </div>
              <div>
                <dt>Repetitions</dt>
                <dd>{e.repetitions}</dd>
              </div>
              <div>
                <dt>Provider budget</dt>
                <dd>{money(e.budget_usd)}</dd>
              </div>
            </dl>
          </Panel>
          <Panel
            title="System configurations"
            detail="Provider credentials are environment references, never stored key values."
          >
            <div className="panel-body">
              {e.systems.map((s) => (
                <JsonView key={s.id} label={s.name} value={s} />
              ))}
            </div>
          </Panel>
        </>
      )}
    </>
  );
}
function TrialEvidence({ trial: t }: { trial: Trial }) {
  return (
    <div className="trial-evidence">
      {t.error && <Notice tone="error">{t.error}</Notice>}
      <div className="evidence-metrics">
        <span>
          Score <strong>{percent(t.score)}</strong>
        </span>
        <span>
          Input tokens <strong>{t.input_tokens ?? '—'}</strong>
        </span>
        <span>
          Output tokens <strong>{t.output_tokens ?? '—'}</strong>
        </span>
        <span>
          Started <strong>{date(t.started_at)}</strong>
        </span>
      </div>
      {t.checks.length > 0 && (
        <div className="evidence-checks">
          <h3>Evaluation checks</h3>
          {t.checks.map((c) => (
            <div className={`evidence-check ${c.passed ? 'pass' : 'fail'}`} key={c.id}>
              <div className="evidence-check-title">
                {c.passed ? <CheckCircle2 size={17} /> : <XCircle size={17} />}
                <strong>{c.name}</strong>
                <span className="pill">{c.kind}</span>
                {c.required && <span className="required-check">Required</span>}
              </div>
              <p>{c.message}</p>
              <div className="expected-actual">
                <JsonView value={c.expected} label="Expected" />
                <JsonView value={c.actual} label="Actual" />
              </div>
            </div>
          ))}
        </div>
      )}
      <JsonView label="Submitted output" value={t.output} open />
      {t.finished_at && <TrialReviews experimentId={t.experiment_id} trialId={t.id} />}
      <div className="evidence-id">
        Trial <code>{t.id}</code>
      </div>
    </div>
  );
}
function ReportView({ report: r, systems }: { report: Report; systems: System[] }) {
  const name = (id: string) => systems.find((s) => s.id === id)?.name ?? id;
  return (
    <>
      {r.warnings.length > 0 && (
        <details className="report-notes">
          <summary>
            <ShieldCheck size={16} />
            <strong>Interpreting these results</strong>
            <span>{r.warnings.length} notes</span>
            <ChevronDown size={16} />
          </summary>
          <ul>
            {r.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
      <Panel
        title="System comparison"
        detail="Pass rates and uncertainty from the recorded task cohort. Missing values are shown as —."
      >
        <div className="table-scroll">
          <table className="comparison-table">
            <thead>
              <tr>
                <th>System</th>
                <th>Pass rate · 95% interval</th>
                <th>Passed / scheduled</th>
                <th>Errors</th>
                <th>Cost</th>
                <th>Cost / pass</th>
                <th>Median latency</th>
              </tr>
            </thead>
            <tbody>
              {r.systems.map((s) => (
                <tr key={s.system_id}>
                  <td>
                    <strong>{s.name}</strong>
                    {['reference', 'incomplete'].includes(
                      systems.find((system) => system.id === s.system_id)?.kind ?? '',
                    ) && <span className="table-secondary fixture-label">Fixture control</span>}
                  </td>
                  <td>
                    <div className="rate-value" title={s.interval_method}>
                      {percent(s.pass_rate)}
                      <span>
                        {s.ci_low != null && s.ci_high != null
                          ? `${percent(s.ci_low)}–${percent(s.ci_high)}`
                          : 'Interval unavailable'}
                      </span>
                    </div>
                    <div
                      className="interval-track"
                      role="img"
                      aria-label={`Pass rate ${percent(s.pass_rate)}, interval ${percent(s.ci_low)} to ${percent(s.ci_high)}`}
                    >
                      {s.ci_low != null && s.ci_high != null && (
                        <span
                          className="interval-range"
                          style={{
                            left: `${s.ci_low * 100}%`,
                            width: `${(s.ci_high - s.ci_low) * 100}%`,
                          }}
                        />
                      )}
                      {s.pass_rate != null && (
                        <span
                          className="interval-point"
                          style={{ left: `${s.pass_rate * 100}%` }}
                        />
                      )}
                    </div>
                  </td>
                  <td className="tabular">
                    {s.passed} <span className="text-muted">/ {s.scheduled}</span>
                    <span className="table-secondary">{s.completed} completed</span>
                  </td>
                  <td>{s.errors}</td>
                  <td>
                    {money(s.cost_usd)}
                    {!s.cost_complete && <span className="table-secondary">Incomplete cost</span>}
                  </td>
                  <td>{money(s.cost_per_pass)}</td>
                  <td>
                    {duration(s.median_duration_ms)}
                    <span className="table-secondary">p95 {duration(s.p95_duration_ms)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="table-footnote">
          <ShieldCheck size={15} />
          {r.total_trials} scheduled · {r.completed_trials} completed · {r.incomplete_trials}{' '}
          incomplete. Intervals describe this cohort; they do not guarantee performance on new work.
        </div>
      </Panel>
      {r.comparisons.length > 0 && (
        <Panel
          title="Paired comparisons"
          detail="Differences on shared tasks. Repetitions of the same task are not independent task samples."
        >
          <div className="paired-grid">
            {r.comparisons.map((c, i) => (
              <div className="paired-card" key={i}>
                <h3>
                  {name(c.left)} <span>vs.</span> {name(c.right)}
                </h3>
                <div className="paired-difference">
                  {c.difference == null
                    ? '—'
                    : `${c.difference > 0 ? '+' : ''}${(c.difference * 100).toFixed(1)} pp`}
                  <span>pass-rate difference</span>
                </div>
                <p>{c.conclusion}</p>
                <div className="paired-footnote">
                  {c.paired_tasks} paired tasks · Interval{' '}
                  {c.ci_low == null || c.ci_high == null
                    ? 'unavailable'
                    : `${(c.ci_low * 100).toFixed(1)} to ${(c.ci_high * 100).toFixed(1)} pp`}
                </div>
              </div>
            ))}
          </div>
        </Panel>
      )}
      {r.domains.length > 0 && (
        <Panel
          title="Performance by domain"
          detail="Keep task mix visible when interpreting aggregate performance."
        >
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>System</th>
                  <th>Trials</th>
                  <th>Pass rate</th>
                </tr>
              </thead>
              <tbody>
                {r.domains.map((d, i) => (
                  <tr key={i}>
                    <td>
                      <DomainBadge domain={d.domain} />
                    </td>
                    <td>{name(d.system_id)}</td>
                    <td>{d.trials}</td>
                    <td className="tabular">{percent(d.pass_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
