import { useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  EyeOff,
  LockKeyhole,
  Plus,
  RefreshCw,
  UnlockKeyhole,
} from 'lucide-react';
import { getToken, post } from '../api';
import {
  Button,
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
import { errorMessage } from '../format';
import type { ReviewBrief, Rubric, TeamUser } from '../team-types';
import { CreateRubric } from './ReviewWorkbench';
import { useRemote } from './useRemote';

export interface BlindStudy {
  id: string;
  queue_id: string;
  name: string;
  status: 'collecting' | 'locked' | 'revealed';
  mode: 'trusted_custodian';
  created_at: string;
  locked_at: string | null;
  revealed_at: string | null;
  evidence_hash: string;
  items: number;
  assigned_reviews: number;
  completed_reviews: number;
  missing_reviews: number;
  boundary: string;
}
interface StudyReport {
  review: ReviewBrief;
  study: BlindStudy;
  reveal: { reveal_rationale: string };
}
interface Catalog {
  team_enabled: boolean;
  experiments: { id: string; name: string; status: string }[];
  rubrics: Rubric[];
  reviewers: TeamUser[];
}
const boundary =
  'Model identities stay hidden from reviewers and report readers until reveal. Workspace administrators remain trusted; this mode does not hide prompts from a model provider.';

export function BlindStudiesPage({ canManage }: { canManage: boolean }) {
  const studies = useRemote<BlindStudy[]>('/blind-studies');
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="EVALUATION INTEGRITY"
        title="Double-blind evaluations"
        description="Judge the work before revealing the model. Freeze the study, collect independent reviews, and lock the evidence."
        actions={
          canManage ? (
            <LinkButton href="#/blind/new">
              <Plus size={15} /> New study
            </LinkButton>
          ) : undefined
        }
      />
      <div className="blind-workflow" aria-label="Study workflow">
        <div>
          <span>01</span>
          <strong>Freeze the study</strong>
          <p>Same tasks. Fixed rubric. Assigned reviewers.</p>
        </div>
        <div>
          <span>02</span>
          <strong>Review without identities</strong>
          <p>Randomized responses and private judgments.</p>
        </div>
        <div>
          <span>03</span>
          <strong>Lock, then reveal</strong>
          <p>Complete reviews before opening the results.</p>
        </div>
      </div>
      <Notice>{boundary}</Notice>
      {studies.error && <Notice tone="error">{studies.error}</Notice>}
      {studies.busy && <Loading text="Loading studies" />}
      {studies.data?.length === 0 && (
        <EmptyState
          title="Your first blind study starts here"
          description="Choose a completed comparison, a scoring rubric, and at least two reviewers. Reviewers use their existing review desk."
          action={
            canManage ? <LinkButton href="#/blind/new">Create a study</LinkButton> : undefined
          }
        />
      )}
      {(studies.data ?? []).map((study) => (
        <a
          className="review-inbox-row"
          key={study.id}
          href={'#/blind/' + encodeURIComponent(study.id)}
        >
          <span className="review-item-mark">
            <EyeOff size={19} />
          </span>
          <div>
            <h2>{study.name}</h2>
            <p>
              {study.completed_reviews} of {study.assigned_reviews} judgments recorded ·{' '}
              {study.items} task pairs
            </p>
          </div>
          <Status value={study.status} />
          <ArrowRight size={17} />
        </a>
      ))}
      <Panel
        title="Open software. Private evaluation material."
        detail="The study workflow, checks, and reporting code are open source. Your test cases, model credentials, identity mappings, and unreleased results remain in your deployment."
      >
        <div className="team-panel-body">
          <p className="muted">
            Hardware-backed confidential execution is a separate capability and is not available in
            this release. It requires a verified protected runner and participation from the model
            owner.
          </p>
        </div>
      </Panel>
    </div>
  );
}

export function NewBlindStudy() {
  const catalog = useRemote<Catalog>('/blind-studies/catalog');
  const [name, setName] = useState('');
  const [experimentId, setExperimentId] = useState('');
  const [systemIds, setSystemIds] = useState<string[]>([]);
  const [reviewerIds, setReviewerIds] = useState<string[]>([]);
  const [rubricId, setRubricId] = useState('');
  const [showRubric, setShowRubric] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const experiment = useRemote<{ snapshot: { systems: { id: string; name: string }[] } }>(
    experimentId ? '/experiments/' + encodeURIComponent(experimentId) : null,
  );
  return (
    <div className="team-stack">
      <a className="back-link" href="#/blind">
        <ArrowLeft size={15} /> Double-blind evaluations
      </a>
      <PageHeader
        eyebrow="NEW BLIND STUDY"
        title="Set the rules before judging"
        description="Freeze a pair of recorded model outputs and a rubric. Assign at least two independent reviewer identities."
      />
      <Notice>{boundary}</Notice>
      {catalog.error && <Notice tone="error">{catalog.error}</Notice>}
      {catalog.busy && <Loading />}
      {catalog.data && !catalog.data.team_enabled && (
        <Notice tone="warning">
          Enable team access and create reviewer accounts first.{' '}
          <a href="#/team">Open Team access.</a>
        </Notice>
      )}
      <Panel title="Study setup">
        <form
          className="team-panel-body team-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            try {
              const result = await post<BlindStudy>('/blind-studies', {
                name,
                experiment_id: experimentId,
                system_ids: systemIds,
                rubric_id: rubricId,
                reviewer_ids: reviewerIds,
                mode: 'trusted_custodian',
              });
              location.hash = '/blind/' + encodeURIComponent(result.id);
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field
            label="Study name"
            hint="Hidden from report readers until reveal, to avoid clues in the title."
          >
            <input
              required
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            label="Completed experiment"
            hint="This study reviews saved outputs. It makes no new model calls."
          >
            <select
              required
              value={experimentId}
              onChange={(event) => {
                setExperimentId(event.target.value);
                setSystemIds([]);
              }}
            >
              <option value="">Choose an experiment</option>
              {(catalog.data?.experiments ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
          {experiment.error && <Notice tone="error">{experiment.error}</Notice>}
          {experiment.data && (
            <fieldset className="choice-fieldset">
              <legend>Models to compare · choose two</legend>
              {experiment.data.snapshot.systems.map((item) => (
                <label key={item.id}>
                  <input
                    type="checkbox"
                    checked={systemIds.includes(item.id)}
                    disabled={!systemIds.includes(item.id) && systemIds.length === 2}
                    onChange={(event) =>
                      setSystemIds(
                        event.target.checked
                          ? [...systemIds, item.id]
                          : systemIds.filter((id) => id !== item.id),
                      )
                    }
                  />
                  {item.name}
                </label>
              ))}
            </fieldset>
          )}
          <Field label="Scoring rubric">
            <select required value={rubricId} onChange={(event) => setRubricId(event.target.value)}>
              <option value="">Choose a rubric</option>
              {(catalog.data?.rubrics ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.version}
                </option>
              ))}
            </select>
          </Field>
          <Button type="button" variant="ghost" onClick={() => setShowRubric(!showRubric)}>
            <Plus size={14} />
            {showRubric ? 'Close rubric editor' : 'Create a rubric'}
          </Button>
          <fieldset className="choice-fieldset">
            <legend>Independent reviewers · choose at least two</legend>
            {(catalog.data?.reviewers ?? []).map((item) => (
              <label key={item.id}>
                <input
                  type="checkbox"
                  checked={reviewerIds.includes(item.id)}
                  onChange={(event) =>
                    setReviewerIds(
                      event.target.checked
                        ? [...reviewerIds, item.id]
                        : reviewerIds.filter((id) => id !== item.id),
                    )
                  }
                />
                {item.name}
              </label>
            ))}
            {!catalog.data?.reviewers.length && (
              <p>
                Add reviewer accounts in Team access. Operators and administrators cannot be study
                reviewers.
              </p>
            )}
          </fieldset>
          <p className="muted">
            Review task text, rubric instructions, and outputs for identifying clues before creating
            the study. Choose reviewers who have not seen the named results. Assignments are frozen
            when the study starts.
          </p>
          {error && <Notice tone="error">{error}</Notice>}
          <Button
            type="submit"
            busy={busy}
            disabled={
              !catalog.data?.team_enabled ||
              systemIds.length !== 2 ||
              reviewerIds.length < 2 ||
              !rubricId
            }
          >
            <LockKeyhole size={15} /> Freeze and assign study
          </Button>
        </form>
      </Panel>
      {showRubric && (
        <CreateRubric
          onSaved={(id) => {
            catalog.reload();
            setRubricId(id);
            setShowRubric(false);
          }}
        />
      )}
    </div>
  );
}

export function BlindStudyDetail({ id, canManage }: { id: string; canManage: boolean }) {
  const result = useRemote<BlindStudy>('/blind-studies/' + encodeURIComponent(id));
  const [rationale, setRationale] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const study = result.data;
  const report = useRemote<StudyReport>(
    study?.status === 'revealed' ? '/blind-studies/' + encodeURIComponent(id) + '/report' : null,
  );
  async function transition(action: 'lock' | 'reveal') {
    if (!study) return;
    setBusy(action);
    setError('');
    try {
      await post('/blind-studies/' + encodeURIComponent(id) + '/' + action, {
        expected_hash: study.evidence_hash,
        ...(action === 'reveal' ? { rationale } : {}),
      });
      result.reload();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy('');
    }
  }
  async function download(format: 'html' | 'json') {
    setBusy(format);
    setError('');
    try {
      const headers = new Headers();
      const token = getToken();
      if (token) headers.set('Authorization', 'Bearer ' + token);
      const response = await fetch(
        '/api/blind-studies/' + encodeURIComponent(id) + '/report?format=' + format,
        { headers, credentials: 'same-origin' },
      );
      if (!response.ok) {
        if (response.status === 401) window.dispatchEvent(new Event('workspace:auth-required'));
        throw new Error('The study report could not be downloaded.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'everval-blind-study.' + format;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy('');
    }
  }
  if (result.error) return <Notice tone="error">{result.error}</Notice>;
  if (!study) return <Loading text="Loading study" />;
  return (
    <div className="team-stack">
      <a className="back-link" href="#/blind">
        <ArrowLeft size={15} /> All blind studies
      </a>
      <PageHeader
        eyebrow="DOUBLE-BLIND EVALUATION"
        title={study.name}
        description={boundary}
        actions={
          <Button variant="secondary" disabled={!!busy} onClick={result.reload}>
            <RefreshCw size={14} /> Refresh
          </Button>
        }
      />
      <div className="blind-state-bar">
        {(['collecting', 'locked', 'revealed'] as const).map((phase, index) => (
          <span key={phase} aria-current={phase === study.status ? 'step' : undefined}>
            <b>{index + 1}</b>
            {phase === 'collecting'
              ? 'Collect reviews'
              : phase === 'locked'
                ? 'Judgments locked'
                : 'Results revealed'}
          </span>
        ))}
      </div>
      <div className="review-brief-metrics">
        <Panel title="Review coverage">
          <div className="team-panel-body">
            <strong className="metric-number">
              {study.completed_reviews} / {study.assigned_reviews}
            </strong>
            <p>assigned judgments recorded</p>
          </div>
        </Panel>
        <Panel title="Frozen workload">
          <div className="team-panel-body">
            <strong className="metric-number">{study.items}</strong>
            <p>task pairs · two selected models</p>
          </div>
        </Panel>
        <Panel title="Study phase">
          <div className="team-panel-body">
            <Status value={study.status} />
            <p>
              {study.status === 'revealed'
                ? 'Identified results released'
                : 'Identities and results withheld'}
            </p>
          </div>
        </Panel>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {study.status === 'collecting' && (
        <Panel
          title="Judgments come first"
          detail="Reviewers score their assigned pairs in the review desk. They cannot see model identities or one another’s judgments."
        >
          <div className="team-panel-body team-stack">
            <p>
              {study.missing_reviews
                ? `${study.missing_reviews} judgments remain before the study can be locked.`
                : 'Every required judgment is recorded. The coordinator can now lock the study.'}
            </p>
            {canManage && (
              <>
                <p className="muted">
                  Locking freezes the recorded judgments. New judgments and revisions will be
                  rejected.
                </p>
                <Button
                  busy={busy === 'lock'}
                  disabled={!!busy || study.missing_reviews !== 0}
                  onClick={() => void transition('lock')}
                >
                  <LockKeyhole size={15} /> Lock completed judgments
                </Button>
              </>
            )}
          </div>
        </Panel>
      )}
      {study.status === 'locked' && (
        <Panel
          title="Judgments are locked"
          detail="Scores are fixed. Identities and results remain withheld until the coordinator records the reveal."
        >
          <div className="team-panel-body team-stack">
            {canManage ? (
              <form
                className="team-stack"
                onSubmit={(event) => {
                  event.preventDefault();
                  void transition('reveal');
                }}
              >
                <Field
                  label="Reason for revealing results"
                  hint="Retained with the release record."
                >
                  <textarea
                    required
                    maxLength={10000}
                    rows={3}
                    value={rationale}
                    onChange={(event) => setRationale(event.target.value)}
                  />
                </Field>
                <Button
                  type="submit"
                  busy={busy === 'reveal'}
                  disabled={!!busy || !rationale.trim()}
                >
                  <UnlockKeyhole size={15} /> Reveal identities and results
                </Button>
              </form>
            ) : (
              <p>The study coordinator controls when the results are released.</p>
            )}
          </div>
        </Panel>
      )}
      {study.status === 'revealed' && (
        <Panel
          title="Released study report"
          detail="These results are bound to the locked judgments. Human reviews remain separate from automated scores."
        >
          <div className="team-panel-body team-stack">
            <div className="header-actions">
              <Button variant="secondary" disabled={!!busy} onClick={() => void download('html')}>
                <Download size={15} /> HTML report
              </Button>
              <Button variant="secondary" disabled={!!busy} onClick={() => void download('json')}>
                <Download size={15} /> JSON evidence
              </Button>
              <LinkButton href={'#/reviews/queues/' + encodeURIComponent(study.queue_id)} secondary>
                Decision brief
              </LinkButton>
            </div>
            {report.error && <Notice tone="error">{report.error}</Notice>}
            {report.data && (
              <>
                {report.data.review.fixture_controls_present && (
                  <Notice tone="warning">
                    This study uses installation fixture controls. It demonstrates the review
                    workflow, not model quality.
                  </Notice>
                )}
                <Panel
                  title="Recorded preferences"
                  detail="Judgments are votes, not independent model trials. Multiple reviewers can judge the same task."
                >
                  <div className="team-panel-body">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th scope="col">Model / outcome</th>
                          <th scope="col">Judgments</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(report.data.review.preferences).map(([name, count]) => (
                          <tr key={name}>
                            <td>{name}</td>
                            <td>{count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="muted">{report.data.review.interpretation}</p>
                  </div>
                </Panel>
                <p>
                  <strong>Release reason:</strong> {report.data.reveal.reveal_rationale}
                </p>
                <JsonView value={report.data} label="Frozen protocol and released evidence" />
              </>
            )}
          </div>
        </Panel>
      )}
      <details className="panel blind-boundary">
        <summary>What this protects</summary>
        <p>{boundary}</p>
        <p>
          Source text can still identify a model. Independent reviewers and neutral study material
          are required. The local audit log records the workflow; it does not attest a confidential
          hardware environment.
        </p>
      </details>
    </div>
  );
}
