import { useEffect, useId, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import {
  Button,
  EmptyState,
  Field,
  JsonView,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Status,
} from '../components';
import { money, percent } from '../format';
import type {
  TrainingCheckpoint,
  TrainingComparison,
  TrainingDashboard,
  TrainingMetric,
  TrainingRunSummary,
} from '../training-types';
import { useTrainingSnapshot } from './useTrainingSnapshot';

export function trainingLag(seconds: number | null): string {
  if (seconds == null) return 'Unknown';
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}
function provenance(kind: string): string {
  return kind === 'synthetic_control'
    ? 'Synthetic control — not model performance'
    : kind === 'declared_training'
      ? 'Declared training — weight identity not independently verified'
      : kind.replaceAll('_', ' ') + ' — provenance declared by its author';
}
function metricAt(checkpoint: TrainingCheckpoint, domain: string) {
  return checkpoint.metrics.find((metric) => metric.domain === domain);
}
function interval(metric: TrainingMetric) {
  return metric.lower == null || metric.upper == null
    ? 'Interval unavailable'
    : `Reported interval ${percent(metric.lower)}–${percent(metric.upper)}`;
}
function metricSummary(metric: TrainingMetric | undefined) {
  if (!metric) return 'Not scheduled';
  return `${metric.value == null ? 'No score' : percent(metric.value)} · ${metric.n}/${metric.expected_n} graded · ${metric.status.replaceAll('_', ' ')}`;
}

/** Gaps are explicit: a missing checkpoint never becomes a zero or a joined line. */
export function TrainingTrajectory({
  checkpoints,
  domain,
  selected,
  onSelect,
  provenanceLabel,
}: {
  checkpoints: TrainingCheckpoint[];
  domain: string;
  selected: string;
  onSelect: (id: string) => void;
  provenanceLabel: string;
}) {
  const titleId = useId();
  const descId = useId();
  const left = 64,
    right = 720,
    top = 28,
    bottom = 214;
  const firstStep = checkpoints[0]?.step ?? 0;
  const lastStep = checkpoints.at(-1)?.step ?? firstStep;
  const x = (step: number) =>
    lastStep === firstStep
      ? (left + right) / 2
      : left + ((step - firstStep) / (lastStep - firstStep)) * (right - left);
  const y = (value: number) => bottom - Math.max(0, Math.min(1, value)) * (bottom - top);
  const points = checkpoints.map((checkpoint) => ({
    checkpoint,
    metric: metricAt(checkpoint, domain),
  }));
  return (
    <figure className="training-trajectory">
      <svg viewBox="0 0 780 270" role="img" aria-labelledby={titleId} aria-describedby={descId}>
        <title id={titleId}>{domain}: equal-family pass rate across training steps</title>
        <desc id={descId}>
          Dots show equal-family pass rates. Vertical whiskers show reported intervals. Missing
          scores are marked with a cross below the axis. The checkpoint table provides every value
          and selection control.
        </desc>
        {[0, 0.25, 0.5, 0.75, 1].map((value) => (
          <g key={value}>
            <line x1={left} x2={right} y1={y(value)} y2={y(value)} className="training-gridline" />
            <text x={left - 12} y={y(value) + 4} textAnchor="end">
              {Math.round(value * 100)}%
            </text>
          </g>
        ))}
        {points.map(({ checkpoint, metric }, index) => {
          const previous = points[index - 1];
          const value = metric?.value;
          return (
            <g
              key={checkpoint.id}
              className={selected === checkpoint.id ? 'training-point selected' : 'training-point'}
            >
              {value != null && previous?.metric?.value != null && (
                <line
                  x1={x(previous.checkpoint.step)}
                  y1={y(previous.metric.value)}
                  x2={x(checkpoint.step)}
                  y2={y(value)}
                  className="training-connection"
                />
              )}
              {value != null && metric?.lower != null && metric?.upper != null && (
                <g className="training-interval">
                  <line
                    x1={x(checkpoint.step)}
                    x2={x(checkpoint.step)}
                    y1={y(metric.lower)}
                    y2={y(metric.upper)}
                  />
                  <line
                    x1={x(checkpoint.step) - 5}
                    x2={x(checkpoint.step) + 5}
                    y1={y(metric.lower)}
                    y2={y(metric.lower)}
                  />
                  <line
                    x1={x(checkpoint.step) - 5}
                    x2={x(checkpoint.step) + 5}
                    y1={y(metric.upper)}
                    y2={y(metric.upper)}
                  />
                </g>
              )}
              {value == null ? (
                <text
                  x={x(checkpoint.step)}
                  y={bottom + 20}
                  textAnchor="middle"
                  className="training-gap"
                >
                  ×
                </text>
              ) : (
                <circle
                  cx={x(checkpoint.step)}
                  cy={y(value)}
                  r={selected === checkpoint.id ? 6 : 4}
                >
                  <title>
                    Step {checkpoint.step}: {metricSummary(metric)}
                  </title>
                </circle>
              )}
            </g>
          );
        })}
        <text x={left} y={258}>
          Step {firstStep}
        </text>
        {lastStep !== firstStep && (
          <text x={right} y={258} textAnchor="end">
            Step {lastStep}
          </text>
        )}
      </svg>
      <figcaption>
        <strong>{provenanceLabel}.</strong> Equal-family pass rate · whiskers: reported intervals ·
        ×: score missing. Intervals describe individual checkpoints; regression decisions use the
        paired analysis below.
      </figcaption>
      <div className="training-step-buttons" aria-label="Select checkpoint">
        {checkpoints.map((checkpoint) => (
          <button
            key={checkpoint.id}
            type="button"
            aria-pressed={selected === checkpoint.id}
            onClick={() => onSelect(checkpoint.id)}
          >
            Step {checkpoint.step}
          </button>
        ))}
      </div>
    </figure>
  );
}
function pointDelta(value: number | null) {
  return value == null ? 'Unknown' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(1)} pp`;
}
function Comparisons({ records }: { records: TrainingComparison[] }) {
  return (
    <Panel
      title="Regression decisions"
      detail="Matched task-family analysis; practical thresholds and repeated comparisons are accounted for."
    >
      {!records.length && (
        <p>No saved comparisons. Absence of an alert does not establish equivalence.</p>
      )}
      {records.map((record) => (
        <article key={record.id} className="training-record">
          <h3>
            Step {record.baseline_step} → {record.candidate_step}
          </h3>
          <p>Inference scope: {record.inference_scope ?? 'not declared'}</p>
          <p>
            Planned comparison {record.look_index} / {record.max_looks}
          </p>
          {record.synthetic_control && (
            <p>
              <strong>Synthetic control — not model performance</strong>
            </p>
          )}
          {record.stale && (
            <Notice tone="warning">
              Stale comparison: source evidence changed. Recheck the evidence before using this
              decision.
            </Notice>
          )}
          {record.domains.map((domain) => (
            <div key={domain.domain} className="training-decision">
              <h4>{domain.domain}</h4>
              <Status value={record.stale ? 'stale_evidence' : domain.decision} />
              <p>
                Evaluation qualification:{' '}
                {domain.qualification?.status.replaceAll('_', ' ') ?? 'not supplied'} · researcher
                review required.
              </p>
              <p>
                Observed difference: {pointDelta(domain.delta)} · paired interval:{' '}
                {domain.interval ? domain.interval.map(pointDelta).join(' to ') : 'Unavailable'}
              </p>
              <p>
                {domain.matched_pairs}/{domain.expected_pairs} matched pairs · {domain.clusters}{' '}
                task families
              </p>
              {!!domain.reasons?.length && (
                <ul>
                  {domain.reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {!!domain.execution_contract_issues?.length && (
                <ul>
                  {domain.execution_contract_issues.map((issue) => (
                    <li key={issue}>{issue}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <JsonView value={record} label={`Inspect comparison at step ${record.candidate_step}`} />
        </article>
      ))}
    </Panel>
  );
}
function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}
function EvidenceRecords({
  title,
  records,
  empty,
}: {
  title: string;
  records: Record<string, unknown>[];
  empty: string;
}) {
  return (
    <Panel title={title} className="training-records">
      {!records.length ? (
        <p>{empty}</p>
      ) : (
        records.map((record, index) => (
          <article key={String(record.id ?? index)} className="training-record">
            {record.stale === true && (
              <Notice tone="warning">
                Stale evidence: the source results changed. Review this record before using its
                recommendation.
              </Notice>
            )}
            {typeof record.status === 'string' && <Status value={record.status} />}
            {typeof record.decision === 'string' && <Status value={record.decision} />}
            {typeof record.candidate_step === 'number' && (
              <p>Candidate step {record.candidate_step}</p>
            )}
            {record.synthetic_control === true && <p>Synthetic control — not model performance</p>}
            {typeof record.domain === 'string' && <h3>{record.domain}</h3>}
            {[
              'observations',
              'recommendation',
              'rationale',
              'reason',
              'summary',
              'attribution',
              'evidence_origin',
            ].map((key) =>
              typeof record[key] === 'string' ? <p key={key}>{record[key]}</p> : null,
            )}
            {['hypotheses', 'next_checks'].map((key) =>
              stringList(record[key]).length > 0 ? (
                <div key={key}>
                  <h4>{key.replaceAll('_', ' ')}</h4>
                  <ul>
                    {stringList(record[key]).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null,
            )}
            {record.result != null &&
              typeof record.result === 'object' &&
              'status' in record.result &&
              typeof record.result.status === 'string' && <Status value={record.result.status} />}
            {record.result != null &&
              typeof record.result === 'object' &&
              'gaps' in record.result && (
                <ul>
                  {stringList(record.result.gaps).map((gap) => (
                    <li key={gap}>{gap}</li>
                  ))}
                </ul>
              )}
            <JsonView value={record} label={`Inspect ${title.toLowerCase()} record ${index + 1}`} />
          </article>
        ))
      )}
    </Panel>
  );
}
function TrainingRunView({ data }: { data: TrainingDashboard }) {
  const checkpoints = [...data.checkpoints].sort((a, b) => a.step - b.step);
  const domains = data.run.protocol.domains;
  const [domainChoice, setDomain] = useState('');
  const [checkpointChoice, setCheckpoint] = useState('');
  const domain = domains.some((item) => item.name === domainChoice)
    ? domainChoice
    : (domains[0]?.name ?? '');
  const selected = checkpoints.find((item) => item.id === checkpointChoice) ?? checkpoints.at(-1);
  const unknown = data.budget.unknown_cost_count;
  return (
    <>
      <Notice tone={data.run.provenance_kind === 'synthetic_control' ? 'warning' : 'info'}>
        <strong>{provenance(data.run.provenance_kind)}</strong>
        <br />
        Inference scope: {data.run.protocol.inference_scope ?? 'not declared'}.
        <br />
        Training identity: {data.run.training_run_id}. Local records do not independently verify a
        remote model's weights.
      </Notice>
      <p className="training-inference-note">
        Intervals assume independent task families and the frozen comparison policy. Adaptive tuning
        on reused evaluation families can invalidate coverage. Results do not certify deployment
        readiness.
      </p>
      {data.budget.admission_blocked && (
        <Notice tone="warning">
          <strong>New paid admissions blocked by accounting review.</strong>
          <ul>
            {data.budget.blocking_reasons?.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          {data.budget.conservative_commitment_usd != null && (
            <p>Conservative commitment: {money(data.budget.conservative_commitment_usd)}</p>
          )}
        </Notice>
      )}
      <div className="training-summary">
        <div>
          <span>Recorded checkpoints</span>
          <strong>{checkpoints.length}</strong>
        </div>
        <div>
          <span>Evaluation domains</span>
          <strong>{domains.length}</strong>
        </div>
        <div>
          <span>{unknown ? 'Known spend · lower bound' : 'Known spend'}</span>
          <strong>{money(data.budget.known_cost_usd)}</strong>
          <small>{unknown} cost entries unavailable</small>
        </div>
        <div>
          <span>Allocated / lifetime budget</span>
          <strong>
            {money(data.budget.allocated_usd)} / {money(data.budget.lifetime_budget_usd)}
          </strong>
        </div>
      </div>
      {!checkpoints.length ? (
        <Panel>
          <EmptyState
            title="No checkpoints registered"
            description="Register a checkpoint with its exact weight digest and frozen system configuration to track its evaluation."
          />
        </Panel>
      ) : (
        <>
          <Panel
            title="Checkpoint trajectory"
            detail="Training step is the horizontal axis. Coverage and failed attempts remain visible."
            action={
              <Field label="Evaluation domain">
                <select value={domain} onChange={(event) => setDomain(event.target.value)}>
                  {domains.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </Field>
            }
          >
            {domain && (
              <TrainingTrajectory
                checkpoints={checkpoints}
                domain={domain}
                selected={selected?.id ?? ''}
                onSelect={setCheckpoint}
                provenanceLabel={provenance(data.run.provenance_kind)}
              />
            )}
          </Panel>
          <Panel
            title="Evaluation coverage"
            detail="Select a step to inspect its exact identity. Missing scores are never imputed."
          >
            <div className="training-table-scroll">
              <table className="training-matrix">
                <caption>Checkpoint coverage by evaluation domain</caption>
                <thead>
                  <tr>
                    <th scope="col">Checkpoint</th>
                    {domains.map((item) => (
                      <th scope="col" key={item.name}>
                        {item.name}
                      </th>
                    ))}
                    <th scope="col">Evaluation lag</th>
                    <th scope="col">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {checkpoints.map((checkpoint) => (
                    <tr
                      key={checkpoint.id}
                      className={selected?.id === checkpoint.id ? 'selected' : ''}
                    >
                      <th scope="row">
                        <button
                          type="button"
                          aria-pressed={selected?.id === checkpoint.id}
                          onClick={() => setCheckpoint(checkpoint.id)}
                        >
                          Inspect step {checkpoint.step}
                        </button>
                        <Status value={checkpoint.status} />
                      </th>
                      {domains.map((item) => {
                        const metric = metricAt(checkpoint, item.name);
                        return (
                          <td key={item.name}>
                            <strong>
                              {metric?.value == null ? 'No score' : percent(metric.value)}
                            </strong>
                            <span>
                              {metric ? `${metric.n}/${metric.expected_n} graded` : 'Not scheduled'}
                            </span>
                            <span>
                              {metric?.status.replaceAll('_', ' ') ?? 'Missing evaluation'}
                            </span>
                            {metric && (metric.errors > 0 || metric.missing > 0) && (
                              <span>
                                {metric.errors} errors · {metric.missing} missing
                              </span>
                            )}
                          </td>
                        );
                      })}
                      <td>{trainingLag(checkpoint.lag_seconds)}</td>
                      <td>
                        {checkpoint.unknown_cost_count > 0 ? '≥ ' : ''}
                        {money(checkpoint.known_cost_usd)}
                        <span>{checkpoint.unknown_cost_count} unknown</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          {selected && (
            <Panel
              title={`Checkpoint evidence · step ${selected.step}`}
              className="training-checkpoint"
            >
              <dl>
                <dt>Checkpoint</dt>
                <dd>{selected.id}</dd>
                <dt>Weight SHA-256</dt>
                <dd>
                  <code>{selected.weight_sha256}</code>
                </dd>
                <dt>Frozen system</dt>
                <dd>
                  {selected.system_id} · <code>{selected.system_hash}</code>
                </dd>
                <dt>Recorded</dt>
                <dd>{selected.created_at}</dd>
                <dt>Evaluation lag</dt>
                <dd>
                  {trainingLag(selected.lag_seconds)} · registration to completion, or to now while
                  unfinished
                </dd>
              </dl>
              <div className="training-domain-details">
                {domains.map((item) => {
                  const metric = metricAt(selected, item.name);
                  return (
                    <article key={item.name}>
                      <h3>{item.name}</h3>
                      <p>{metricSummary(metric)}</p>
                      {metric && (
                        <>
                          <p>
                            {interval(metric)}
                            {metric.interval_method && <> · {metric.interval_method}</>}
                          </p>
                          <p>
                            Scheduled-attempt pass rate:{' '}
                            {percent(metric.scheduled_pass_rate ?? null)} (distinct from the
                            family-weighted chart).
                          </p>
                          <p>
                            {metric.errors} errors · {metric.missing} missing ·{' '}
                            {metric.cost_complete
                              ? 'Cost complete'
                              : 'Cost incomplete; known spend is a lower bound'}
                          </p>
                          {metric.experiment_id && (
                            <a
                              className="text-link"
                              href={`#/experiments/${encodeURIComponent(metric.experiment_id)}`}
                            >
                              Open {item.name} experiment
                            </a>
                          )}
                        </>
                      )}
                    </article>
                  );
                })}
              </div>
            </Panel>
          )}
        </>
      )}
      <div className="training-evidence-grid">
        <Comparisons records={data.comparisons} />
        <EvidenceRecords title="Alerts" records={data.alerts} empty="No retained alerts." />
        <EvidenceRecords
          title="Investigations"
          records={data.investigations}
          empty="No retained investigation. Attribution requires matched evidence and a reviewed recommendation."
        />
        <EvidenceRecords
          title="Evaluation qualifications"
          records={data.qualifications}
          empty="No qualification evidence attached."
        />
        <EvidenceRecords
          title="Evaluation mix studies"
          records={data.mix_studies}
          empty="No evaluation mix selection recorded."
        />
        <EvidenceRecords
          title="Held-out mix validation"
          records={data.mix_validations ?? []}
          empty="No held-out evaluation mix validation recorded."
        />
      </div>
      <Panel
        title="Frozen evaluation protocol"
        detail="Measurement and spending assumptions are explicit and inspectable."
      >
        <p>
          Baseline step {data.run.protocol.baseline_step} · {data.run.protocol.max_looks} planned
          comparisons · family-wise alpha {data.run.protocol.alpha} · minimum{' '}
          {data.run.protocol.min_clusters} task families.
        </p>
        <JsonView value={data.run.protocol} label="Inspect frozen protocol" />
      </Panel>
    </>
  );
}
export function TrainingPage() {
  const runs = useTrainingSnapshot<TrainingRunSummary[]>('/training/runs');
  const [choice, setChoice] = useState('');
  const runId = runs.data?.some((run) => run.id === choice) ? choice : (runs.data?.[0]?.id ?? '');
  const dashboard = useTrainingSnapshot<TrainingDashboard>(
    runId ? `/training/runs/${encodeURIComponent(runId)}` : null,
  );
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        runs.reload();
        dashboard.reload();
      }
    }, 15000);
    return () => window.clearInterval(timer);
  }, [runs.reload, dashboard.reload]);
  return (
    <div className="training-page">
      <PageHeader
        eyebrow="POST-TRAINING OBSERVABILITY"
        title="Training evaluations"
        description="Follow checkpoint quality, measurement coverage, and regression investigations."
        actions={
          <Button
            variant="secondary"
            busy={runs.busy || dashboard.busy}
            onClick={() => {
              runs.reload();
              dashboard.reload();
            }}
          >
            <RefreshCw size={15} />
            Refresh training evidence
          </Button>
        }
      />
      <p className="training-refresh-note">
        Read-only monitoring · refreshes every 15 seconds while visible · no model calls from this
        view.
      </p>
      {runs.error && (
        <Notice tone="error">
          {runs.error}
          {runs.data && ' Showing the last successful run list; refresh failed.'}
        </Notice>
      )}
      {!runs.data && runs.busy && <Loading text="Loading training runs" />}
      {runs.data && !runs.data.length && (
        <Panel>
          <EmptyState
            title="Start with a frozen training protocol"
            description="Register a training run with named domains, task families, comparison steps and a budget using the training CLI. Then register checkpoints and queue their evaluations. No demonstration scores are inserted into this workspace."
          />
        </Panel>
      )}
      {!!runs.data?.length && (
        <>
          <Field label="Training run">
            <select
              value={runId}
              onChange={(event) => {
                setChoice(event.target.value);
              }}
            >
              {runs.data.map((run) => (
                <option value={run.id} key={run.id}>
                  {run.name} · {run.provenance_kind} · {run.checkpoint_count} checkpoints
                </option>
              ))}
            </select>
          </Field>
          {dashboard.error && (
            <Notice tone="error">
              {dashboard.error}
              {dashboard.data && ' Showing the last successful snapshot; refresh failed.'}
            </Notice>
          )}
          {dashboard.data?.generated_at && (
            <p className="training-refresh-note">
              Snapshot generated: {dashboard.data.generated_at}
            </p>
          )}
          {!dashboard.data && dashboard.busy && <Loading text="Loading checkpoint evidence" />}
          {dashboard.data && <TrainingRunView data={dashboard.data} key={dashboard.data.run.id} />}
        </>
      )}
    </div>
  );
}
