import { useState } from 'react';
import { ArrowLeft, ArrowRight, FileUp, Plus } from 'lucide-react';
import { post, request } from '../api';
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
import type { CaseBatch, ProductionCase } from '../team-types';
import { EvidenceValue } from './EvidenceValue';
import { useRemote } from './useRemote';

export function ProductionImports() {
  const imports = useRemote<CaseBatch[]>('/case-imports');
  const [file, setFile] = useState<File | null>(null);
  const [fields, setFields] = useState(
    'password, token, api_key, authorization, email, customer_email, secret',
  );
  const [literals, setLiterals] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="team-stack">
      <PageHeader
        eyebrow="REAL-WORLD REGRESSIONS"
        title="Production examples"
        description="Turn reviewed incidents into repeatable tasks. An observed response is evidence of what happened—not an answer key."
      />
      <Panel title="Stage examples for review" detail="CSV, JSONL, or typed JSON · up to 500 cases">
        <form
          className="team-panel-body team-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!file) return;
            setBusy(true);
            setError('');
            const body = new FormData();
            body.set('file', file);
            body.set(
              'redacted_fields',
              JSON.stringify(
                fields
                  .split(',')
                  .map((value) => value.trim())
                  .filter(Boolean),
              ),
            );
            body.set('redacted_literals', JSON.stringify(literals.split('\n').filter(Boolean)));
            try {
              const batch = await request<CaseBatch>('/case-imports', { method: 'POST', body });
              setLiterals('');
              location.hash = '/cases/' + batch.id;
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Examples file">
            <input
              required
              type="file"
              accept=".csv,.jsonl,.json"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </Field>
          <details>
            <summary>File format</summary>
            <p>
              Each row needs <code>name</code>, <code>domain</code> (documents, data, or workflow),{' '}
              <code>instruction</code>, and <code>inputs</code>. Optional fields are{' '}
              <code>observed_output</code> and <code>source_id</code>. CSV object cells contain
              JSON. JSON accepts an array or an object with a <code>cases</code> array.
            </p>
            <p>Reference answers and checks are supplied separately during approval.</p>
          </details>
          <Field
            label="Redact fields"
            hint="Comma-separated field names, matched at every nesting level."
          >
            <input value={fields} onChange={(event) => setFields(event.target.value)} />
          </Field>
          <Field
            label="Redact exact text"
            hint="One sensitive value per line. Values are replaced before storage and are not retained as redaction rules."
          >
            <textarea
              rows={3}
              value={literals}
              onChange={(event) => setLiterals(event.target.value)}
              autoComplete="off"
            />
          </Field>
          <Notice>
            Inspect every staged case before approval. Field and exact-text redaction cannot
            guarantee removal of all sensitive information.
          </Notice>
          {error && <Notice tone="error">{error}</Notice>}
          <Button type="submit" busy={busy} disabled={!file}>
            <FileUp size={16} /> Redact and stage examples
          </Button>
        </form>
      </Panel>
      <Panel title="Staged imports">
        <div className="team-panel-body team-stack">
          {imports.error && <Notice tone="error">{imports.error}</Notice>}
          {imports.busy && <Loading />}
          {imports.data?.length === 0 && (
            <EmptyState
              compact
              title="No production examples imported"
              description="Start with a small set of authorized, representative incidents."
            />
          )}
          {imports.data?.map((batch) => (
            <a className="review-inbox-row" href={'#/cases/' + batch.id} key={batch.id}>
              <div>
                <h2>{batch.filename}</h2>
                <p>
                  {batch.count} cases · {new Date(batch.created_at).toLocaleDateString()}
                </p>
              </div>
              <ArrowRight size={16} />
            </a>
          ))}
        </div>
      </Panel>
    </div>
  );
}
export function ProductionBatch({ id, refresh }: { id: string; refresh: () => Promise<void> }) {
  const batch = useRemote<CaseBatch>('/case-imports/' + encodeURIComponent(id));
  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<string | null>(null);
  if (batch.error) return <Notice tone="error">{batch.error}</Notice>;
  if (!batch.data) return <Loading />;
  const data = batch.data;
  const approved = data.cases.filter((item) => item.status === 'approved');
  const item = data.cases.find((value) => value.id === selected);
  return (
    <div className="team-stack">
      <a className="back-link" href="#/cases">
        <ArrowLeft size={15} /> Production examples
      </a>
      <PageHeader
        eyebrow="REVIEW BEFORE PUBLISHING"
        title={data.filename}
        description={data.privacy_note}
      />
      <div className="case-layout">
        <Panel title="Case ledger" detail={data.cases.length + ' staged examples'}>
          <div className="case-ledger">
            {data.cases.map((entry) => (
              <button
                key={entry.id}
                className={'case-choice ' + (selected === entry.id ? 'selected' : '')}
                onClick={() => setSelected(entry.id)}
              >
                <strong>{entry.name}</strong>
                <span>
                  {entry.domain} <Status value={entry.status} />
                </span>
              </button>
            ))}
          </div>
        </Panel>
        {item ? (
          <CaseApproval key={item.id} item={item} onApproved={() => batch.reload()} />
        ) : (
          <Panel>
            <EmptyState
              title="Choose a case to review"
              description="Inspect the redacted inputs, then independently define the behavior and checks that should be correct."
            />
          </Panel>
        )}
      </div>
      <Panel
        title="Publish a validated collection"
        detail="Only approved, unpublished cases are included."
      >
        <form
          className="team-panel-body team-stack"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError('');
            try {
              const result = await post<{ collection: { id: string } }>(
                '/case-imports/' + id + '/publish',
                { name, case_ids: approved.map((value) => value.id) },
              );
              setPublished(result.collection.id);
              batch.reload();
              await refresh();
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <p>
            {approved.length} approved cases are ready to publish. Every reference and negative
            control is revalidated.
          </p>
          <Field label="Collection name">
            <input
              required
              maxLength={200}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          {error && <Notice tone="error">{error}</Notice>}
          {published && (
            <Notice tone="success">
              Collection published.{' '}
              <a href={'#/collections/' + published}>Open the validated collection.</a>
            </Notice>
          )}
          <Button type="submit" busy={busy} disabled={!approved.length}>
            Publish {approved.length} approved {approved.length === 1 ? 'case' : 'cases'}
          </Button>
        </form>
      </Panel>
    </div>
  );
}
interface ExpectedCheck {
  path: string;
  kind: 'equals' | 'numeric' | 'contains';
  value: string;
}
function approvedReference(checks: ExpectedCheck[]) {
  const reference: Record<string, unknown> = {};
  const definitions = checks.map((check, index) => {
    const expected = check.kind === 'numeric' ? Number(check.value) : check.value;
    if (check.kind === 'numeric' && (!check.value.trim() || !Number.isFinite(expected)))
      throw new Error('Numeric expected values must be finite numbers.');
    const parts = check.path.split('.');
    if (parts.some((part) => !part || ['__proto__', 'prototype', 'constructor'].includes(part)))
      throw new Error('Use ordinary dotted output fields.');
    let target = reference;
    for (const part of parts.slice(0, -1)) {
      const current = target[part];
      if (current !== undefined && (current === null || typeof current !== 'object'))
        throw new Error('Output paths cannot overlap scalar values.');
      target[part] = current ?? {};
      target = target[part] as Record<string, unknown>;
    }
    target[parts[parts.length - 1]] = expected;
    return {
      name: 'Expected ' + check.path,
      id: 'approved_' + (index + 1),
      kind: check.kind,
      path: check.path,
      expected,
      required: true,
    };
  });
  return { reference_output: reference, checks: definitions };
}
function CaseApproval({ item, onApproved }: { item: ProductionCase; onApproved: () => void }) {
  const [behavior, setBehavior] = useState('');
  const [rationale, setRationale] = useState('');
  const [checks, setChecks] = useState<ExpectedCheck[]>([
    { path: 'answer', kind: 'equals', value: '' },
  ]);
  const [advanced, setAdvanced] = useState(false);
  const [referenceJSON, setReferenceJSON] = useState('{}');
  const [checksJSON, setChecksJSON] = useState('[]');
  const [controlsJSON, setControlsJSON] = useState('[{}]');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <Panel
      title={item.name}
      detail="Approve intended behavior independently of the observed response."
    >
      <div className="team-panel-body team-stack">
        <p className="task-instruction">{item.instruction}</p>
        <details open>
          <summary>Redacted task inputs</summary>
          <EvidenceValue value={item.inputs} />
        </details>
        <details>
          <summary>Observed output · not ground truth</summary>
          <EvidenceValue value={item.observed_output} />
        </details>
        {item.status !== 'staged' ? (
          <>
            <Notice tone="success">This case has an immutable approval.</Notice>
            <p>{item.approval?.expected_behavior}</p>
            <p className="muted">{item.approval?.rationale}</p>
            {item.collection_id && (
              <LinkButton href={'#/collections/' + item.collection_id} secondary>
                Open collection
              </LinkButton>
            )}
          </>
        ) : (
          <form
            className="team-stack"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError('');
              try {
                const expected = advanced
                  ? { reference_output: JSON.parse(referenceJSON), checks: JSON.parse(checksJSON) }
                  : approvedReference(checks);
                await post('/production-cases/' + item.id + '/approve', {
                  expected_behavior: behavior,
                  rationale,
                  ...expected,
                  negative_controls: JSON.parse(controlsJSON),
                });
                onApproved();
              } catch (err) {
                setError(errorMessage(err));
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="What should the correct behavior be?">
              <textarea
                required
                rows={3}
                maxLength={10000}
                value={behavior}
                onChange={(event) => setBehavior(event.target.value)}
              />
            </Field>
            <Field
              label="Why is this expectation correct?"
              hint="Record the independent source or human reasoning behind the answer."
            >
              <textarea
                required
                rows={2}
                value={rationale}
                onChange={(event) => setRationale(event.target.value)}
              />
            </Field>
            <label className="check-label">
              <input
                type="checkbox"
                checked={advanced}
                onChange={(event) => setAdvanced(event.target.checked)}
              />
              Use full structured reference and check definitions
            </label>
            {!advanced ? (
              <>
                {checks.map((check, index) => (
                  <fieldset key={index} className="rubric-dimension">
                    <legend>Expected output {index + 1}</legend>
                    <div className="team-form-grid">
                      <Field
                        label={'Output field ' + (index + 1)}
                        hint="A dotted field in the submitted response, such as total or result.answer."
                      >
                        <input
                          required
                          value={check.path}
                          onChange={(event) =>
                            setChecks(
                              checks.map((value, i) =>
                                i === index ? { ...value, path: event.target.value } : value,
                              ),
                            )
                          }
                        />
                      </Field>
                      <Field label={'Comparison ' + (index + 1)}>
                        <select
                          value={check.kind}
                          onChange={(event) =>
                            setChecks(
                              checks.map((value, i) =>
                                i === index
                                  ? { ...value, kind: event.target.value as ExpectedCheck['kind'] }
                                  : value,
                              ),
                            )
                          }
                        >
                          <option value="equals">Exact text</option>
                          <option value="numeric">Number</option>
                          <option value="contains">Contains text</option>
                        </select>
                      </Field>
                    </div>
                    <Field label={'Approved expected value ' + (index + 1)}>
                      <textarea
                        required
                        rows={2}
                        value={check.value}
                        onChange={(event) =>
                          setChecks(
                            checks.map((value, i) =>
                              i === index ? { ...value, value: event.target.value } : value,
                            ),
                          )
                        }
                      />
                    </Field>
                  </fieldset>
                ))}
                <Button
                  type="button"
                  variant="ghost"
                  disabled={checks.length >= 20}
                  onClick={() => setChecks([...checks, { path: '', kind: 'equals', value: '' }])}
                >
                  <Plus size={15} />
                  Add another expected field
                </Button>
              </>
            ) : (
              <>
                <Field label="Reference output JSON">
                  <textarea
                    required
                    rows={5}
                    value={referenceJSON}
                    onChange={(event) => setReferenceJSON(event.target.value)}
                  />
                </Field>
                <Field label="Checks JSON">
                  <textarea
                    required
                    rows={6}
                    value={checksJSON}
                    onChange={(event) => setChecksJSON(event.target.value)}
                  />
                </Field>
              </>
            )}
            <details>
              <summary>Negative controls</summary>
              <p>
                Every control must fail the approved checks. The empty response is included by
                default; add known incorrect responses when helpful.
              </p>
              <Field label="Negative control outputs JSON">
                <textarea
                  rows={3}
                  value={controlsJSON}
                  onChange={(event) => setControlsJSON(event.target.value)}
                />
              </Field>
            </details>
            {error && <Notice tone="error">{error}</Notice>}
            <Button type="submit" busy={busy}>
              Validate and approve expected behavior
            </Button>
          </form>
        )}
      </div>
    </Panel>
  );
}
