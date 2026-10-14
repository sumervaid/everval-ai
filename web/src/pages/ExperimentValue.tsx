import { useState } from 'react';
import { post } from '../api';
import { Button, EmptyState, Loading, Notice, Panel, Status } from '../components';
import { duration, errorMessage, money, percent } from '../format';
import type { SystemReport, Trial } from '../types';
import { EvidenceValue } from './EvidenceValue';
import { useRemote } from './useRemote';

interface EconomicSystem extends SystemReport {
  eligible_for_frontier: boolean;
  dominated_by: string[];
  known_spend_on_nonpassing_attempts: number;
  cost_per_scheduled_attempt: number | null;
  fixture_control: boolean;
  submission_replay: boolean;
  expensive_attempts: {
    trial_id: string;
    scenario_name: string;
    cost_usd: number;
    passed: boolean | null;
  }[];
}
interface Economics {
  systems: EconomicSystem[];
  observed_frontier: string[];
  evidence_issues: string[];
  notes: string[];
}
export function EconomicView({ id }: { id: string }) {
  const report = useRemote<Economics>('/experiments/' + encodeURIComponent(id) + '/economics');
  if (report.error) return <Notice tone="error">{report.error}</Notice>;
  if (!report.data) return <Loading />;
  const data = report.data;
  return (
    <div className="team-stack">
      <Panel
        title="Quality, cost, and time"
        detail="Compare the same frozen workload using recorded measurements."
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>System</th>
                <th>Pass rate</th>
                <th>Cost / passing attempt</th>
                <th>P95 duration</th>
                <th>Known spend on nonpassing attempts</th>
                <th>Observed tradeoff</th>
              </tr>
            </thead>
            <tbody>
              {data.systems.map((system) => (
                <tr key={system.system_id}>
                  <td>
                    <strong>{system.name}</strong>
                    {system.fixture_control && (
                      <span className="table-secondary">Fixture control</span>
                    )}
                    {system.submission_replay && (
                      <span className="table-secondary">Imported evidence</span>
                    )}
                  </td>
                  <td>{percent(system.pass_rate)}</td>
                  <td>{money(system.cost_per_pass)}</td>
                  <td>{duration(system.p95_duration_ms)}</td>
                  <td>{money(system.known_spend_on_nonpassing_attempts)}</td>
                  <td>
                    {!system.eligible_for_frontier
                      ? 'Insufficient eligible evidence'
                      : data.observed_frontier.includes(system.system_id)
                        ? 'On observed frontier'
                        : 'Dominated in observed metrics'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Notice>
        The frontier describes this recorded sample. It does not establish a statistically reliable
        winner or recommend routing future tasks.
      </Notice>
      {data.evidence_issues.length > 0 && (
        <Notice tone="warning">{data.evidence_issues.join(' ')}</Notice>
      )}
      <details className="panel team-panel-body">
        <summary>How to interpret this view</summary>
        <ul>
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </details>
      {data.systems.map((system) => (
        <Panel key={system.system_id} title={system.name + ' · largest recorded charges'}>
          <div className="team-panel-body">
            {!system.expensive_attempts.length ? (
              <p className="muted">No recorded charges are available.</p>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Task</th>
                      <th>Recorded cost</th>
                      <th>Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {system.expensive_attempts.map((attempt) => (
                      <tr key={attempt.trial_id}>
                        <td>{attempt.scenario_name}</td>
                        <td>{money(attempt.cost_usd)}</td>
                        <td>
                          {attempt.passed === true
                            ? 'Passed'
                            : attempt.passed === false
                              ? 'Did not pass'
                              : 'Unknown'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Panel>
      ))}
    </div>
  );
}
interface Failure {
  trial_id: string;
  scenario_name: string;
  system_name: string;
  category: string;
  next_action: string;
  error: string | null;
  failed_checks: number;
  cost_usd: number | null;
  duration_ms: number | null;
}
interface Triage {
  counts: Record<string, number>;
  failures: Failure[];
  classification_basis: string;
}
interface Diagnostic {
  trial: Trial;
  category: string;
  next_action: string;
  file_diffs: { path: string; patch: string; truncated: boolean }[];
  diffs_truncated: boolean;
  regrades: { id: string; passed: boolean | null; created_at: string; error: string | null }[];
  reproduction: { available: boolean; command: string[]; note: string };
}
export function FailureView({ id }: { id: string }) {
  const triage = useRemote<Triage>('/experiments/' + encodeURIComponent(id) + '/triage');
  const [selected, setSelected] = useState<string | null>(null);
  if (triage.error) return <Notice tone="error">{triage.error}</Notice>;
  if (!triage.data) return <Loading />;
  return (
    <div className="team-stack">
      <Panel title="Failure inbox" detail={triage.data.classification_basis}>
        <div className="team-panel-body">
          <div className="failure-counts">
            {Object.entries(triage.data.counts).map(([category, count]) => (
              <span key={category}>
                <strong>{count}</strong> {category.replaceAll('_', ' ')}
              </span>
            ))}
          </div>
        </div>
        {!triage.data.failures.length && (
          <EmptyState
            compact
            title="No failing or incomplete attempts"
            description="Every recorded attempt passed its required checks."
          />
        )}
        <div className="failure-list">
          {triage.data.failures.map((failure) => (
            <article className="failure-card" key={failure.trial_id}>
              <div>
                <Status value={failure.category} />
                <h3>{failure.scenario_name}</h3>
                <p className="muted">
                  {failure.system_name} · {failure.failed_checks} failed checks ·{' '}
                  {money(failure.cost_usd)}
                </p>
                <p>{failure.next_action}</p>
                {failure.error && <pre className="evidence-code">{failure.error}</pre>}
              </div>
              <Button variant="secondary" onClick={() => setSelected(failure.trial_id)}>
                Inspect this attempt
              </Button>
            </article>
          ))}
        </div>
      </Panel>
      {selected && <DiagnosticDetail key={selected} experimentId={id} trialId={selected} />}
    </div>
  );
}
function DiagnosticDetail({ experimentId, trialId }: { experimentId: string; trialId: string }) {
  const endpoint =
    '/experiments/' + encodeURIComponent(experimentId) + '/trials/' + encodeURIComponent(trialId);
  const diagnostics = useRemote<Diagnostic>(endpoint + '/diagnostics');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (diagnostics.error) return <Notice tone="error">{diagnostics.error}</Notice>;
  if (!diagnostics.data) return <Loading />;
  const data = diagnostics.data;
  return (
    <Panel title={data.trial.scenario_name} detail={data.next_action}>
      <div className="team-panel-body team-stack">
        <details open>
          <summary>Recorded output</summary>
          <EvidenceValue value={data.trial.output} />
        </details>
        <details>
          <summary>Check evidence</summary>
          <EvidenceValue value={data.trial.checks} />
        </details>
        {data.file_diffs.map((diff) => (
          <details key={diff.path}>
            <summary>
              {diff.path}
              {diff.truncated ? ' · truncated' : ''}
            </summary>
            <pre className="evidence-code">{diff.patch}</pre>
          </details>
        ))}
        {data.diffs_truncated && (
          <Notice>
            Only the first thirty changed files are shown here. Full outputs remain in the
            experiment evidence.
          </Notice>
        )}
        <div>
          <h3>Reproduce grading locally</h3>
          <p>{data.reproduction.note}</p>
          <code className="reproduction-command">{data.reproduction.command.join(' ')}</code>
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        <Button
          disabled={!data.reproduction.available}
          busy={busy}
          onClick={async () => {
            setBusy(true);
            setError('');
            try {
              await post(endpoint + '/regrade');
              diagnostics.reload();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          Regrade recorded output · no model call
        </Button>
        <div>
          <h3>Retained regrades</h3>
          {!data.regrades.length ? (
            <p className="muted">No diagnostic regrades yet.</p>
          ) : (
            data.regrades.map((regrade) => (
              <p key={regrade.id}>
                <strong>
                  {regrade.passed === true
                    ? 'Passed'
                    : regrade.passed === false
                      ? 'Did not pass'
                      : 'Unavailable'}
                </strong>{' '}
                · {new Date(regrade.created_at).toLocaleString()}
                {regrade.error ? ' · ' + regrade.error : ''}
              </p>
            ))
          )}
        </div>
      </div>
    </Panel>
  );
}
