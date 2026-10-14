import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check, EyeOff, Plus, Users } from 'lucide-react';
import { post } from '../api';
import {
  Button,
  EmptyState,
  Field,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
  Status,
} from '../components';
import { errorMessage } from '../format';
import type { Experiment, Workspace } from '../types';
import type {
  QueueSummary,
  ReviewAssignment,
  ReviewBrief,
  ReviewItem,
  Rubric,
  Session,
  TeamUser,
} from '../team-types';
import { EvidenceValue } from './EvidenceValue';
import { useRemote } from './useRemote';

export function ReviewInbox({ session }: { session: Session }) {
  const inbox = useRemote<ReviewAssignment[]>('/review-inbox');
  const canManage = session.capabilities.includes('workspace.write');
  const items = inbox.data ?? [];
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="HUMAN JUDGMENT"
        title="Your review desk"
        description="Read the task, compare the responses, and record your judgment against a frozen rubric."
        actions={
          canManage ? (
            <LinkButton href="#/reviews/queues" secondary>
              Manage queues
            </LinkButton>
          ) : undefined
        }
      />
      {!session.team_enabled && (
        <Notice>
          Assigned reviews require personal identities. An administrator can enable team access from
          the local console using <code>everval team bootstrap --name NAME</code>.
        </Notice>
      )}
      {inbox.error && <Notice tone="error">{inbox.error}</Notice>}
      {inbox.busy && <Loading />}
      {!!items.length && (
        <div className="review-progress">
          <strong>
            {items.filter((item) => item.reviewed).length} of {items.length}
          </strong>
          <span> assigned reviews complete</span>
        </div>
      )}
      {inbox.data && !items.length && (
        <EmptyState
          title="No reviews assigned"
          description="Your coordinator can assign a queue to this identity. You will only see the tasks assigned to you."
        />
      )}
      <div className="review-inbox">
        {items.map((item) => (
          <a
            key={item.id}
            className="review-inbox-row"
            href={'#/reviews/items/' + encodeURIComponent(item.id)}
          >
            <span className={'review-item-mark ' + (item.reviewed ? 'done' : '')}>
              {item.reviewed ? <Check size={18} /> : <Users size={18} />}
            </span>
            <div>
              <span className="eyebrow">{item.queue_name}</span>
              <h2>{item.name}</h2>
              <p>
                {item.domain}
                {item.blind ? ' · System identities withheld' : ''}
              </p>
            </div>
            <span className="review-row-status">
              {item.reviewed ? 'Reviewed' : 'Ready to review'} <ArrowRight size={16} />
            </span>
          </a>
        ))}
      </div>
    </div>
  );
}

export function ReviewTask({ id }: { id: string }) {
  const result = useRemote<ReviewItem>('/review-items/' + encodeURIComponent(id));
  if (result.error)
    return (
      <Notice tone="error">
        {result.error} <a href="#/reviews">Return to your queue.</a>
      </Notice>
    );
  if (!result.data) return <Loading />;
  return <ReviewTaskForm key={result.data.id} item={result.data} />;
}
function ReviewTaskForm({ item }: { item: ReviewItem }) {
  const labels = Object.keys(item.responses);
  const [scores, setScores] = useState<Record<string, Record<string, string>>>(() =>
    Object.fromEntries(
      labels.map((label) => [
        label,
        Object.fromEntries(
          item.rubric.dimensions.map((dimension) => [
            dimension.id,
            item.previous?.scores[label]?.[dimension.id]?.toString() ?? '',
          ]),
        ),
      ]),
    ),
  );
  const [preference, setPreference] = useState(item.previous?.preference ?? '');
  const [disposition, setDisposition] = useState(item.previous?.disposition ?? '');
  const [rationale, setRationale] = useState(item.previous?.rationale ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      await post('/review-items/' + encodeURIComponent(item.id) + '/judgments', {
        scores: Object.fromEntries(
          Object.entries(scores).map(([label, values]) => [
            label,
            Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])),
          ]),
        ),
        preference: labels.length === 2 ? preference : null,
        disposition,
        rationale,
      });
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="team-stack">
      <a className="back-link" href="#/reviews">
        <ArrowLeft size={15} /> Your review desk
      </a>
      <PageHeader
        eyebrow={item.queue_name}
        title={item.task.name}
        description="Your judgment is saved under your authenticated identity. Automated scores are kept separate."
      />
      {item.blind && (
        <Notice>
          <EyeOff size={16} /> {item.blinding_note}
        </Notice>
      )}
      <Panel title="The task" className="review-task-panel">
        <div className="team-panel-body">
          <p className="task-instruction">{item.task.instruction}</p>
          <details>
            <summary>Supporting inputs</summary>
            <EvidenceValue value={item.task.inputs} />
          </details>
        </div>
      </Panel>
      <div className={'review-responses ' + (labels.length === 1 ? 'single' : '')}>
        {labels.map((label) => (
          <Panel key={label} title={'Response ' + label} detail={item.responses[label].name}>
            <div className="team-panel-body response-body">
              <EvidenceValue value={item.responses[label].output} />
            </div>
          </Panel>
        ))}
      </div>
      <Panel title={item.rubric.name} detail={'Frozen rubric · version ' + item.rubric.version}>
        <form onSubmit={submit} className="team-panel-body team-stack">
          <p>{item.rubric.instructions}</p>
          {item.rubric.dimensions.map((dimension) => (
            <fieldset className="rubric-dimension" key={dimension.id}>
              <legend>{dimension.name}</legend>
              <p>{dimension.guidance}</p>
              <div className="team-form-grid">
                {labels.map((label) => (
                  <Field key={label} label={'Response ' + label + ' · ' + dimension.name}>
                    <select
                      required
                      value={scores[label][dimension.id]}
                      onChange={(event) =>
                        setScores({
                          ...scores,
                          [label]: { ...scores[label], [dimension.id]: event.target.value },
                        })
                      }
                    >
                      <option value="">Choose a score</option>
                      <option value="1">1.00 · Fully satisfies</option>
                      <option value="0.75">0.75 · Mostly satisfies</option>
                      <option value="0.5">0.50 · Partly satisfies</option>
                      <option value="0.25">0.25 · Mostly unsatisfied</option>
                      <option value="0">0.00 · Does not satisfy</option>
                    </select>
                  </Field>
                ))}
              </div>
            </fieldset>
          ))}
          {labels.length === 2 && (
            <Field label="Which response better satisfies the task?">
              <select
                required
                value={preference}
                onChange={(event) => setPreference(event.target.value)}
              >
                <option value="">Choose a preference</option>
                <option value="A">Response A</option>
                <option value="B">Response B</option>
                <option value="tie">Tie / no preference</option>
              </select>
            </Field>
          )}
          <Field label="Overall judgment">
            <select
              required
              value={disposition}
              onChange={(event) => setDisposition(event.target.value)}
            >
              <option value="">Choose a judgment</option>
              <option value="acceptable">Acceptable</option>
              <option value="unacceptable">Unacceptable</option>
              <option value="needs_discussion">Needs discussion</option>
            </select>
          </Field>
          <Field
            label="Explain your judgment"
            hint="Describe the evidence that informed your scores."
          >
            <textarea
              required
              rows={4}
              maxLength={10000}
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </Field>
          {error && <Notice tone="error">{error}</Notice>}
          {saved && (
            <Notice tone="success">
              Review recorded. <a href="#/reviews">Return to your review desk.</a>
            </Notice>
          )}
          {item.previous && (
            <p className="muted">
              Previously reviewed by {item.previous.reviewer_name}. Saving again retains the earlier
              judgment.
            </p>
          )}
          <div className="form-actions">
            <Button busy={busy} type="submit">
              {item.previous || saved ? 'Save revised judgment' : 'Record judgment'}
            </Button>
            <LinkButton href="#/reviews" secondary>
              Back to queue
            </LinkButton>
          </div>
        </form>
      </Panel>
    </div>
  );
}

export function ReviewQueueList({ canManage }: { canManage: boolean }) {
  const queues = useRemote<QueueSummary[]>('/review-queues');
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="REVIEW OPERATIONS"
        title="Review queues"
        description="Frozen rubrics and assigned reviewers turn individual judgments into a traceable decision."
        actions={
          canManage ? (
            <LinkButton href="#/reviews/queues/new">Create review queue</LinkButton>
          ) : undefined
        }
      />
      {queues.error && <Notice tone="error">{queues.error}</Notice>}
      {queues.busy && <Loading />}
      {queues.data?.length === 0 && (
        <EmptyState
          title="No review queues yet"
          description="An operator can freeze a completed experiment for assigned review."
        />
      )}
      {(queues.data ?? []).map((queue) => (
        <a className="review-inbox-row" key={queue.id} href={'#/reviews/queues/' + queue.id}>
          <div>
            <h2>{queue.name}</h2>
            <p>
              {queue.blind ? 'Blind comparison' : 'Named responses'} · Created{' '}
              {new Date(queue.created_at).toLocaleDateString()}
            </p>
          </div>
          <ArrowRight size={18} />
        </a>
      ))}
    </div>
  );
}

export function CreateReviewQueue({
  workspace,
  session,
}: {
  workspace: Workspace;
  session: Session;
}) {
  const rubrics = useRemote<Rubric[]>('/review-rubrics');
  const reviewers = useRemote<TeamUser[]>('/team/reviewers');
  const [experimentId, setExperimentId] = useState('');
  const experiment = useRemote<Experiment & { snapshot: { systems: Workspace['systems'] } }>(
    experimentId ? '/experiments/' + encodeURIComponent(experimentId) : null,
  );
  const [name, setName] = useState('');
  const [rubricId, setRubricId] = useState('');
  const [systemIds, setSystemIds] = useState<string[]>([]);
  const [reviewerIds, setReviewerIds] = useState<string[]>([]);
  const [blind, setBlind] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showRubric, setShowRubric] = useState(false);
  const completed = workspace.experiments.filter((run) =>
    ['completed', 'failed', 'cancelled'].includes(run.status),
  );
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="REVIEW OPERATIONS"
        title="Create a review queue"
        description="Select one response or a pair from a completed experiment. Every selected task attempt must have an output."
      />
      {!session.team_enabled && (
        <Notice>
          Enable personal identities first with <code>everval team bootstrap --name NAME</code>,
          then add reviewer accounts under Team access.
        </Notice>
      )}
      <Panel title="Queue setup">
        <form
          className="team-panel-body team-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            try {
              const queue = await post<QueueSummary>('/review-queues', {
                name,
                experiment_id: experimentId,
                rubric_id: rubricId,
                reviewer_ids: reviewerIds,
                system_ids: systemIds,
                blind,
              });
              location.hash = '/reviews/queues/' + queue.id;
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Queue name">
            <input
              required
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Completed experiment">
            <select
              required
              value={experimentId}
              onChange={(event) => {
                setExperimentId(event.target.value);
                setSystemIds([]);
              }}
            >
              <option value="">Choose an experiment</option>
              {completed.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name}
                </option>
              ))}
            </select>
          </Field>
          {experiment.error && <Notice tone="error">{experiment.error}</Notice>}
          {experiment.data && (
            <fieldset className="choice-fieldset">
              <legend>Responses to review · choose one or two</legend>
              {experiment.data.snapshot.systems.map((system) => (
                <label key={system.id}>
                  <input
                    type="checkbox"
                    checked={systemIds.includes(system.id)}
                    disabled={!systemIds.includes(system.id) && systemIds.length === 2}
                    onChange={(event) =>
                      setSystemIds(
                        event.target.checked
                          ? [...systemIds, system.id]
                          : systemIds.filter((id) => id !== system.id),
                      )
                    }
                  />
                  {system.name}
                </label>
              ))}
            </fieldset>
          )}
          <Field label="Frozen rubric">
            <select required value={rubricId} onChange={(event) => setRubricId(event.target.value)}>
              <option value="">Choose a rubric</option>
              {rubrics.data?.map((rubric) => (
                <option key={rubric.id} value={rubric.id}>
                  {rubric.name} · {rubric.version}
                </option>
              ))}
            </select>
          </Field>
          {rubrics.error && <Notice tone="error">{rubrics.error}</Notice>}
          <Button variant="ghost" type="button" onClick={() => setShowRubric(!showRubric)}>
            <Plus size={15} /> {showRubric ? 'Close rubric editor' : 'Create a rubric'}
          </Button>
          <fieldset className="choice-fieldset">
            <legend>Assigned reviewers</legend>
            {reviewers.data?.map((user) => (
              <label key={user.id}>
                <input
                  type="checkbox"
                  checked={reviewerIds.includes(user.id)}
                  onChange={(event) =>
                    setReviewerIds(
                      event.target.checked
                        ? [...reviewerIds, user.id]
                        : reviewerIds.filter((id) => id !== user.id),
                    )
                  }
                />
                {user.name} <span className="muted">{user.role}</span>
              </label>
            ))}
            {!reviewers.data?.length && <p>Add active reviewer identities under Team access.</p>}
          </fieldset>
          {reviewers.error && <Notice tone="error">{reviewers.error}</Notice>}
          <label className="check-label">
            <input
              type="checkbox"
              checked={blind}
              onChange={(event) => setBlind(event.target.checked)}
            />
            Withhold system identities and randomize pair order
          </label>
          <p className="muted">
            The review endpoint excludes private checks, reference answers, system metadata, and
            traces. Response content may still identify its source.
          </p>
          {error && <Notice tone="error">{error}</Notice>}
          <Button
            type="submit"
            busy={busy}
            disabled={!reviewerIds.length || !systemIds.length || !session.team_enabled}
          >
            Freeze and assign queue
          </Button>
        </form>
      </Panel>
      {showRubric && (
        <CreateRubric
          onSaved={(id) => {
            rubrics.reload();
            setRubricId(id);
            setShowRubric(false);
          }}
        />
      )}
    </div>
  );
}
function CreateRubric({ onSaved }: { onSaved: (id: string) => void }) {
  const [name, setName] = useState('');
  const [version, setVersion] = useState('1');
  const [instructions, setInstructions] = useState('');
  const [dimensions, setDimensions] = useState([{ id: 'dimension_1', name: '', guidance: '' }]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="Define the rubric" detail="Criteria and scoring guidance are frozen when saved.">
      <form
        className="team-panel-body team-stack"
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError('');
          try {
            const rubric = await post<Rubric>('/review-rubrics', {
              name,
              version,
              instructions,
              dimensions,
            });
            onSaved(rubric.id);
          } catch (err) {
            setError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="team-form-grid">
          <Field label="Rubric name">
            <input
              required
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Version">
            <input
              required
              maxLength={64}
              value={version}
              onChange={(event) => setVersion(event.target.value)}
            />
          </Field>
        </div>
        <Field label="Reviewer instructions">
          <textarea
            required
            rows={3}
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
          />
        </Field>
        {dimensions.map((dimension, index) => (
          <fieldset className="rubric-dimension" key={dimension.id}>
            <legend>Dimension {index + 1}</legend>
            <Field label={'Dimension ' + (index + 1) + ' name'}>
              <input
                required
                value={dimension.name}
                onChange={(event) =>
                  setDimensions(
                    dimensions.map((item, i) =>
                      i === index ? { ...item, name: event.target.value } : item,
                    ),
                  )
                }
              />
            </Field>
            <Field
              label={'Dimension ' + (index + 1) + ' scoring guidance'}
              hint="Explain what zero, partial credit, and one mean."
            >
              <textarea
                required
                rows={2}
                value={dimension.guidance}
                onChange={(event) =>
                  setDimensions(
                    dimensions.map((item, i) =>
                      i === index ? { ...item, guidance: event.target.value } : item,
                    ),
                  )
                }
              />
            </Field>
          </fieldset>
        ))}
        <Button
          variant="ghost"
          type="button"
          disabled={dimensions.length >= 12}
          onClick={() =>
            setDimensions([
              ...dimensions,
              { id: 'dimension_' + (dimensions.length + 1), name: '', guidance: '' },
            ])
          }
        >
          Add dimension
        </Button>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" busy={busy}>
          Save frozen rubric
        </Button>
      </form>
    </Panel>
  );
}

export function QueueBrief({ id, canManage }: { id: string; canManage: boolean }) {
  const brief = useRemote<ReviewBrief>('/review-queues/' + encodeURIComponent(id) + '/brief');
  const reviewers = useRemote<TeamUser[]>(canManage ? '/team/reviewers' : null);
  const [assigned, setAssigned] = useState<string[]>([]);
  const [outcome, setOutcome] = useState('more_evidence');
  const [rationale, setRationale] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  if (brief.error) return <Notice tone="error">{brief.error}</Notice>;
  if (!brief.data) return <Loading />;
  const data = brief.data;
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="DECISION BRIEF"
        title={data.name}
        description={data.interpretation}
        actions={
          <Button variant="secondary" onClick={() => window.print()}>
            Print brief
          </Button>
        }
      />
      {data.fixture_controls_present && (
        <Notice tone="warning">
          This queue contains installation fixture controls. Their outputs and human judgments
          verify the review workflow; they do not measure model performance.
        </Notice>
      )}
      {data.submission_replays_present && (
        <Notice>
          This queue includes imported submissions. Reviewing or replaying the same outputs adds no
          new independent model attempts.
        </Notice>
      )}
      <div className="review-brief-metrics">
        <Panel title="Review coverage">
          <div className="team-panel-body">
            <strong className="metric-number">
              {data.completed_reviews} / {data.assigned_reviews}
            </strong>
            <p>assigned reviews complete</p>
          </div>
        </Panel>
        <Panel title="Overall judgments">
          <div className="team-panel-body">
            <EvidenceValue value={data.dispositions} />
          </div>
        </Panel>
        <Panel title="Pair preferences">
          <div className="team-panel-body">
            <EvidenceValue value={data.preferences} />
          </div>
        </Panel>
      </div>
      {data.completed_reviews < data.assigned_reviews && (
        <Notice tone="warning">
          Review coverage is incomplete. Any decision retains this coverage in its evidence
          snapshot.
        </Notice>
      )}
      <Panel title={data.rubric.name} detail={'Rubric version ' + data.rubric.version}>
        <div className="team-panel-body">
          <p>{data.rubric.instructions}</p>
          {data.rubric.dimensions.map((dimension) => (
            <p key={dimension.id}>
              <strong>{dimension.name}:</strong> {dimension.guidance}
            </p>
          ))}
        </div>
      </Panel>
      <Panel title="Decision history">
        <div className="team-panel-body team-stack">
          {!data.decisions.length && <p className="muted">No decision has been recorded.</p>}
          {data.decisions.map((decision) => (
            <article className="decision-record" key={decision.id}>
              <Status value={decision.outcome} />
              <p>{decision.rationale}</p>
              <small>
                {new Date(decision.created_at).toLocaleString()} · {decision.actor}
              </small>
            </article>
          ))}
        </div>
      </Panel>
      {error && <Notice tone="error">{error}</Notice>}
      {message && <Notice tone="success">{message}</Notice>}
      {canManage && (
        <Panel title="Record a decision">
          <form
            className="team-panel-body team-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              setMessage('');
              try {
                await post('/review-queues/' + id + '/decisions', {
                  outcome,
                  rationale,
                  evidence_hash: data.evidence_hash,
                });
                setRationale('');
                brief.reload();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="Decision">
              <select value={outcome} onChange={(event) => setOutcome(event.target.value)}>
                <option value="more_evidence">Gather more evidence</option>
                <option value="adopt">Adopt for the stated use</option>
                <option value="reject">Reject for the stated use</option>
              </select>
            </Field>
            <Field label="Decision rationale">
              <textarea
                required
                rows={3}
                value={rationale}
                onChange={(event) => setRationale(event.target.value)}
              />
            </Field>
            <Button type="submit" busy={busy}>
              Record decision with evidence
            </Button>
          </form>
        </Panel>
      )}
      {canManage && (
        <details className="panel team-panel-body">
          <summary>Change assigned reviewers</summary>
          <p>
            Choose the complete replacement assignment. Removed reviewers immediately lose access;
            prior judgments remain retained.
          </p>
          <div className="choice-fieldset">
            {reviewers.data?.map((user) => (
              <label key={user.id}>
                <input
                  type="checkbox"
                  checked={assigned.includes(user.id)}
                  onChange={(event) =>
                    setAssigned(
                      event.target.checked
                        ? [...assigned, user.id]
                        : assigned.filter((value) => value !== user.id),
                    )
                  }
                />
                {user.name}
              </label>
            ))}
          </div>
          <Button
            disabled={!assigned.length}
            busy={busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await post('/review-queues/' + id + '/assignments', { reviewer_ids: assigned });
                brief.reload();
                setMessage('Assignments updated.');
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            Replace assignments
          </Button>
        </details>
      )}
    </div>
  );
}
