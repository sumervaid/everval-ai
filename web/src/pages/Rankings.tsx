import { useState } from 'react';
import { ArrowUpRight, ChartNoAxesColumnIncreasing, Clock3, Coins, RefreshCw } from 'lucide-react';
import {
  Button,
  DomainBadge,
  EmptyState,
  Field,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Status,
} from '../components';
import { date, duration, money, percent } from '../format';
import type {
  RankingBenchmark,
  RankingCell,
  RankingMetric,
  RankingsReport,
  RankingSystem,
} from '../rankings-types';
import { useRemote } from './useRemote';

const metrics = {
  quality: {
    label: 'Quality',
    description: 'Pass rate · higher is better',
    icon: ChartNoAxesColumnIncreasing,
  },
  cost: {
    label: 'Cost per success',
    description: 'USD per passing attempt · lower is better',
    icon: Coins,
  },
  latency: {
    label: 'P95 latency',
    description: 'Recorded attempt duration · lower is better',
    icon: Clock3,
  },
} as const;

function metricValue(cell: RankingCell, metric: RankingMetric) {
  if (metric === 'quality') return percent(cell.pass_rate);
  if (metric === 'cost') return money(cell.cost_complete ? cell.cost_per_success_usd : null);
  return duration(cell.p95_duration_ms);
}

function cellNote(cell: RankingCell, system: RankingSystem, metric: RankingMetric) {
  if (system.fixture_control) return 'Fixture control';
  if (system.submission_replay) return 'Imported evidence';
  if (!cell.eligibility[metric]) {
    if (metric === 'cost' && !cell.cost_complete) return 'Cost incomplete';
    if (metric === 'cost' && cell.passed === 0) return 'No passing attempts';
    if (metric === 'latency' && !cell.duration_complete) return 'Timing incomplete';
    if (cell.completed < cell.scheduled) return 'Run incomplete';
    return 'Unranked evidence';
  }
  return `${cell.passed}/${cell.scheduled} passed`;
}

function tiedRank(cell: RankingCell, metric: RankingMetric, cells: RankingCell[]) {
  const rank = cell.ranks[metric];
  return (
    rank != null &&
    cells.some(
      (other) =>
        other.benchmark_id === cell.benchmark_id &&
        other.system_id !== cell.system_id &&
        other.ranks[metric] === rank,
    )
  );
}

function rankLabel(cell: RankingCell, metric: RankingMetric, cells: RankingCell[]) {
  const rank = cell.ranks[metric];
  return rank == null ? 'Unranked' : `${tiedRank(cell, metric, cells) ? 'Tied ' : ''}#${rank}`;
}

export function RankingsPage() {
  const remote = useRemote<RankingsReport>('/rankings?limit=100');
  const [metric, setMetric] = useState<RankingMetric>('quality');
  const [search, setSearch] = useState('');
  const [benchmarkId, setBenchmarkId] = useState('');
  const [selected, setSelected] = useState<{ benchmark: string; system: string } | null>(null);
  const data = remote.data;
  const benchmarks =
    data?.benchmarks.filter((item) => !benchmarkId || item.id === benchmarkId) ?? [];
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const systems =
    data?.systems.filter((item) =>
      [item.name, item.model, item.kind, item.id]
        .join(' ')
        .toLocaleLowerCase()
        .includes(normalizedSearch),
    ) ?? [];
  const cellIndex = new Map(
    data?.cells.map((cell) => [cell.benchmark_id + ':' + cell.system_id, cell]),
  );
  const selectedCell = selected
    ? cellIndex.get(selected.benchmark + ':' + selected.system)
    : undefined;
  const selectedBenchmark = data?.benchmarks.find((item) => item.id === selectedCell?.benchmark_id);
  const selectedSystem = data?.systems.find((item) => item.id === selectedCell?.system_id);
  const rankedCount = data?.cells.filter((cell) => cell.ranks[metric] != null).length ?? 0;

  return (
    <div className="rankings-page">
      <PageHeader
        eyebrow="YOUR BENCHMARKS, SIDE BY SIDE"
        title="Model rankings"
        description="See which model and configuration performs best on each benchmark you created."
        actions={
          <Button variant="secondary" busy={remote.busy} onClick={remote.reload}>
            <RefreshCw size={15} />
            Refresh results
          </Button>
        }
      />
      {remote.error && <Notice tone="error">{remote.error}</Notice>}
      {!data && remote.busy && <Loading />}
      {data && !data.benchmarks.length && (
        <Panel>
          <EmptyState
            title="Your rankings start with an experiment"
            description="Run models on a frozen collection. Each benchmark becomes a column, and each saved model configuration becomes a row."
            action={<LinkButton href="#/experiments/new">Create an experiment</LinkButton>}
          />
        </Panel>
      )}
      {data && data.benchmarks.length > 0 && (
        <>
          <div className="rankings-overview">
            <div>
              <strong>{data.counts.benchmarks}</strong>
              <span>Frozen benchmarks</span>
            </div>
            <div>
              <strong>{data.counts.systems}</strong>
              <span>Model configurations</span>
            </div>
            <div>
              <strong>{rankedCount}</strong>
              <span>Ranked results · {metrics[metric].label.toLowerCase()}</span>
            </div>
            <p>
              Latest result for each exact configuration and cohort, within the most recent{' '}
              {data.window.limit} runs.
            </p>
          </div>
          {data.counts.experiments_skipped > 0 && (
            <Notice tone="warning">
              {data.counts.experiments_skipped} recent runs could not be verified. Rankings are
              withheld until that evidence can be checked.
            </Notice>
          )}
          <Panel className="rankings-matrix-panel">
            <div className="rankings-toolbar">
              <div className="rankings-metrics" role="group" aria-label="Ranking metric">
                {(Object.keys(metrics) as RankingMetric[]).map((key) => {
                  const Icon = metrics[key].icon;
                  return (
                    <button
                      type="button"
                      key={key}
                      aria-pressed={metric === key}
                      onClick={() => setMetric(key)}
                    >
                      <Icon size={16} />
                      {metrics[key].label}
                    </button>
                  );
                })}
              </div>
              <p id="ranking-metric-description">
                {metrics[metric].description}. Ranks compare results within each column.
              </p>
              <div className="rankings-filters">
                <Field label="Find a model or configuration">
                  <input
                    type="search"
                    value={search}
                    placeholder="Search name, model, or fingerprint"
                    onChange={(event) => {
                      setSearch(event.target.value);
                      setSelected(null);
                    }}
                  />
                </Field>
                <Field label="Benchmark">
                  <select
                    value={benchmarkId}
                    onChange={(event) => {
                      setBenchmarkId(event.target.value);
                      setSelected(null);
                    }}
                  >
                    <option value="">All benchmarks</option>
                    {data.benchmarks.map((benchmark) => (
                      <option key={benchmark.id} value={benchmark.id}>
                        {benchmark.name} · {benchmark.version} · {benchmark.id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
            </div>
            {systems.length === 0 || benchmarks.length === 0 ? (
              <EmptyState
                compact
                title="No matching results"
                description="Try another model name or choose all benchmarks."
              />
            ) : (
              <div
                className="rankings-scroll"
                role="region"
                aria-label="Model ranking matrix"
                tabIndex={0}
              >
                <table
                  className="rankings-matrix"
                  aria-describedby="ranking-metric-description ranking-matrix-note"
                >
                  <caption className="visually-hidden">
                    {metrics[metric].label} by model configuration and frozen benchmark. Select a
                    result to inspect its evidence.
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className="rankings-row-heading">
                        Model / configuration<span>Exact saved configuration</span>
                      </th>
                      {benchmarks.map((benchmark) => (
                        <th scope="col" key={benchmark.id}>
                          <strong>{benchmark.name}</strong>
                          <span>
                            {benchmark.version} · {benchmark.scenario_count} tasks ×{' '}
                            {benchmark.repetitions}
                          </span>
                          <code title={benchmark.id}>{benchmark.id.slice(0, 8)}</code>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {systems.map((system) => (
                      <tr key={system.id}>
                        <th scope="row" className="rankings-row-heading">
                          <strong>{system.name}</strong>
                          <span>{system.model || system.kind}</span>
                          <code title={system.id}>{system.id.slice(0, 8)}</code>
                          {(system.fixture_control || system.submission_replay) && (
                            <span className="rankings-system-kind">
                              {system.fixture_control ? 'Fixture control' : 'Imported evidence'}
                            </span>
                          )}
                          {system.external_adapter && (
                            <span className="rankings-system-kind">External adapter</span>
                          )}
                        </th>
                        {benchmarks.map((benchmark) => {
                          const cell = cellIndex.get(benchmark.id + ':' + system.id);
                          if (!cell)
                            return (
                              <td key={benchmark.id}>
                                <div className="rankings-missing">
                                  <strong>—</strong>
                                  <span>No result in this window</span>
                                </div>
                              </td>
                            );
                          const rank = cell.ranks[metric];
                          const active = selectedCell === cell;
                          return (
                            <td key={benchmark.id}>
                              <button
                                type="button"
                                className={`rankings-cell ${rank === 1 ? 'rankings-best' : rank != null ? 'rankings-ranked' : 'rankings-unranked'}`}
                                aria-pressed={active}
                                aria-controls="ranking-result-detail"
                                aria-label={`${system.name}, ${benchmark.name}, ${metrics[metric].label}: ${metricValue(cell, metric)}, ${rankLabel(cell, metric, data.cells)}`}
                                onClick={() =>
                                  setSelected({ benchmark: benchmark.id, system: system.id })
                                }
                              >
                                <span className="rankings-cell-rank">
                                  {rankLabel(cell, metric, data.cells)}
                                </span>
                                <strong>{metricValue(cell, metric)}</strong>
                                <span>{cellNote(cell, system, metric)}</span>
                                <span>
                                  {cell.completed}/{cell.scheduled} completed
                                </span>
                              </button>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="rankings-legend" id="ranking-matrix-note">
              <span>
                <i className="rankings-key-best" />
                Top observed rank
              </span>
              <span>
                <i className="rankings-key-ranked" />
                Ranked result
              </span>
              <span>
                <i className="rankings-key-unranked" />
                Unranked evidence
              </span>
              <p>
                Select any result for costs, coverage, uncertainty, and its source run. Ties share a
                rank.
              </p>
            </div>
          </Panel>
          <div id="ranking-result-detail" aria-live="polite">
            {selectedCell && selectedBenchmark && selectedSystem ? (
              <ResultDetail
                cell={selectedCell}
                benchmark={selectedBenchmark}
                system={selectedSystem}
              />
            ) : (
              <div className="rankings-selection-hint">
                <ChartNoAxesColumnIncreasing size={20} />
                <p>Select a result above to see the evidence behind its rank.</p>
              </div>
            )}
          </div>
          <Notice>
            Ranks describe observed results on the same frozen workload; they do not establish a
            statistically reliable winner. Controls and imported replays stay visible and unranked.
            Changed cohorts, budgets, execution contracts, or model configurations remain separate.
          </Notice>
          {data.warnings.length > 0 && (
            <details className="rankings-method">
              <summary>How these rankings are calculated</summary>
              <ul>
                {data.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
              <p>
                Costs use the prices saved with each experiment; they are not provider invoice
                totals.
              </p>
            </details>
          )}
          <p className="rankings-window-note">
            Scanned {data.window.scanned_runs} of {data.window.listed_runs} listed runs.
            {data.window.truncated ? ' Older runs are outside this window.' : ''} No score is
            blended across benchmarks. Refresh to load newly saved results.
          </p>
        </>
      )}
    </div>
  );
}

function ResultDetail({
  cell,
  benchmark,
  system,
}: {
  cell: RankingCell;
  benchmark: RankingBenchmark;
  system: RankingSystem;
}) {
  return (
    <Panel
      title={system.name + ' · ' + benchmark.name}
      detail="Selected result · latest matching run in this window"
      action={
        <a
          className="button secondary"
          href={'#/experiments/' + encodeURIComponent(cell.experiment_id)}
        >
          Open experiment
          <ArrowUpRight size={15} />
        </a>
      }
      className="rankings-detail"
    >
      <div className="rankings-detail-body">
        <div className="rankings-detail-meta">
          <DomainBadge domain={benchmark.domain} />
          <Status value={cell.status} />
          <span>{date(cell.created_at)}</span>
        </div>
        <div className="rankings-detail-metrics">
          <div>
            <span>Pass rate</span>
            <strong>{percent(cell.pass_rate)}</strong>
            <p>
              {cell.passed} / {cell.scheduled} scheduled passed
            </p>
            <small>
              {cell.ci_low != null && cell.ci_high != null
                ? `95% interval ${percent(cell.ci_low)}–${percent(cell.ci_high)}`
                : 'Interval unavailable'}
            </small>
          </div>
          <div>
            <span>Cost per success</span>
            <strong>{money(cell.cost_complete ? cell.cost_per_success_usd : null)}</strong>
            <p>{cell.cost_complete ? 'Complete recorded cost' : 'Cost incomplete · not ranked'}</p>
            <small>
              {money(cell.known_cost_usd)} known spend{!cell.cost_complete ? ' · lower bound' : ''}
            </small>
          </div>
          <div>
            <span>P95 latency</span>
            <strong>{duration(cell.p95_duration_ms)}</strong>
            <p>
              {cell.duration_recorded} / {cell.scheduled} timings recorded
            </p>
            <small>
              {cell.duration_complete
                ? 'Complete timing coverage'
                : 'Timing incomplete · not ranked'}
            </small>
          </div>
          <div>
            <span>Completion</span>
            <strong>{percent(cell.completion_rate)}</strong>
            <p>
              {cell.completed} / {cell.scheduled} completed
            </p>
            <small>Failed and incomplete attempts retained</small>
          </div>
        </div>
        {cell.issues.length > 0 && <Notice tone="warning">{cell.issues.join(' ')}</Notice>}
        <dl className="rankings-provenance">
          <div>
            <dt>Source experiment</dt>
            <dd>{cell.experiment_name}</dd>
          </div>
          <div>
            <dt>Frozen benchmark</dt>
            <dd>
              {benchmark.scenario_count} tasks × {benchmark.repetitions} repetitions ·{' '}
              {benchmark.version}
            </dd>
          </div>
          <div>
            <dt>Model / harness</dt>
            <dd>
              {system.model || system.kind} · {system.harness || 'Recorded configuration'}
            </dd>
          </div>
          <div>
            <dt>Benchmark / configuration</dt>
            <dd>
              <code title={benchmark.id}>{benchmark.id.slice(0, 12)}</code> /{' '}
              <code title={system.id}>{system.id.slice(0, 12)}</code>
            </dd>
          </div>
        </dl>
      </div>
    </Panel>
  );
}
