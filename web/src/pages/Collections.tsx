import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Download,
  Check,
  CheckCircle2,
  FileText,
  FolderPlus,
  Plus,
  Search,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import { downloadJson } from '../download';
import { post, request } from '../api';
import type { Collection, Domain, Scenario, Validation, Workspace } from '../types';
import { date, domainLabel, errorMessage, json, parseObject } from '../format';
import {
  Button,
  ConfirmDelete,
  DomainBadge,
  EmptyState,
  Field,
  JsonView,
  LinkButton,
  Loading,
  Notice,
  PageHeader,
  Panel,
} from '../components';
import { RepositoryImport } from './RepositoryImport';
import { StarterButton } from './Workspace';

export function CollectionsPage({
  workspace,
  refresh,
}: {
  workspace: Workspace;
  refresh: () => Promise<void>;
}) {
  const [query, setQuery] = useState('');
  const [domain, setDomain] = useState('all');
  const filtered = workspace.collections.filter(
    (c) =>
      (domain === 'all' || c.domain === domain) &&
      (c.name + ' ' + c.description).toLowerCase().includes(query.toLowerCase()),
  );
  return (
    <>
      <PageHeader
        eyebrow="DEFINE THE WORK"
        title="Collections"
        description="Versioned scenarios with explicit inputs, objectives, and evidence of success."
        actions={
          <LinkButton href="#/collections/new">
            <Plus size={16} />
            New collection
          </LinkButton>
        }
      />
      {workspace.collections.length ? (
        <>
          <div className="filter-bar">
            <label className="search-field">
              <Search size={17} />
              <input
                aria-label="Search collections"
                placeholder="Search collections…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <select
              aria-label="Filter by domain"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
            >
              <option value="all">All domains</option>
              {Object.entries(domainLabel).map(([key, name]) => (
                <option key={key} value={key}>
                  {name}
                </option>
              ))}
            </select>
            <span className="result-count">{filtered.length} collections</span>
          </div>
          <div className="collection-grid">
            {filtered.map((c) => (
              <a
                className="collection-card"
                href={`#/collections/${encodeURIComponent(c.id)}`}
                key={c.id}
              >
                <div className="collection-card-top">
                  <div className="collection-symbol">
                    <FileText size={22} />
                  </div>
                  <DomainBadge domain={c.domain} />
                </div>
                <h2>{c.name}</h2>
                <p>{c.description || 'A versioned collection of evaluation scenarios.'}</p>
                <div className="collection-card-footer">
                  <span>
                    <strong>{c.scenarios.length}</strong> scenarios
                  </span>
                  <span>v{c.version}</span>
                  <ArrowRight size={17} />
                </div>
              </a>
            ))}
          </div>
          {!filtered.length && (
            <EmptyState
              title="No matching collections"
              description="Try a different name or domain filter."
            />
          )}
          <div className="subtle-panel">
            <div>
              <h3>Explore the original starter collections</h3>
              <p>Small, inspectable examples across documents, data, workflows, and code.</p>
            </div>
            <StarterButton secondary refresh={refresh} />
          </div>
        </>
      ) : (
        <Panel>
          <EmptyState
            title="Start with work you can define."
            description="A collection brings together tasks, inputs, and the checks that make results meaningful."
            action={
              <div className="button-row">
                <StarterButton refresh={refresh} />
                <LinkButton href="#/collections/new" secondary>
                  <Upload size={16} />
                  Bring your own collection
                </LinkButton>
              </div>
            }
          />
          <div className="empty-footnote">
            Starter collections contain original fixtures. They demonstrate behavior; they do not
            establish model performance.
          </div>
        </Panel>
      )}
      <div className="principles-strip">
        <ShieldCheck size={18} />
        <strong>Separate the task from the answer.</strong>
        <span>Provider requests exclude evaluation checks and reference outputs.</span>
      </div>
    </>
  );
}
export function CollectionDetail({ id, refresh }: { id: string; refresh: () => Promise<void> }) {
  const [collection, setCollection] = useState<Collection | null>(null);
  const [error, setError] = useState('');
  const [validation, setValidation] = useState<Validation | null>(null);
  const [validating, setValidating] = useState(false);
  const [selected, setSelected] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    request<Collection>(`/collections/${encodeURIComponent(id)}`, { signal: controller.signal })
      .then((c) => {
        setCollection(c);
        setSelected(c.scenarios[0]?.id ?? '');
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorMessage(e));
      });
    return () => controller.abort();
  }, [id]);
  if (error && !collection)
    return (
      <Notice tone="error">
        {error} <a href="#/collections">Back to collections</a>
      </Notice>
    );
  if (!collection) return <Loading text="Loading collection" />;
  const scenario = collection.scenarios.find((s) => s.id === selected);
  const result = validation?.scenarios.find((s) => s.id === selected);
  return (
    <>
      <a className="back-link" href="#/collections">
        <ArrowLeft size={15} />
        All collections
      </a>
      <PageHeader
        eyebrow={`COLLECTION · VERSION ${collection.version}`}
        title={collection.name}
        description={collection.description}
        actions={
          <>
            <Button
              variant="secondary"
              onClick={async () => {
                try {
                  const view = await request(`/collections/${encodeURIComponent(id)}/agent-view`);
                  downloadJson(view, `task-inputs-${id}.json`);
                } catch (err) {
                  setError(errorMessage(err));
                }
              }}
            >
              <Download size={16} />
              Task inputs
            </Button>
            <Button
              variant="secondary"
              busy={validating}
              onClick={async () => {
                setValidating(true);
                setError('');
                try {
                  setValidation(
                    await post<Validation>(`/collections/${encodeURIComponent(id)}/validate`),
                  );
                } catch (e) {
                  setError(errorMessage(e));
                } finally {
                  setValidating(false);
                }
              }}
            >
              <ShieldCheck size={16} />
              Validate collection
            </Button>
            <LinkButton href={`#/experiments/new?collection=${encodeURIComponent(id)}`}>
              <Plus size={16} />
              Create experiment
            </LinkButton>
          </>
        }
      />
      <div className="metadata-row">
        <DomainBadge domain={collection.domain} />
        <span>{collection.scenarios.length} scenarios</span>
        <span>Created {date(collection.created_at)}</span>
        <code>{collection.id}</code>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {validation && (
        <Notice tone={validation.valid ? 'success' : 'warning'}>
          <strong>
            {validation.valid ? 'Validation passed.' : 'Some scenarios need attention.'}
          </strong>{' '}
          {validation.scenarios.filter((s) => s.valid).length} of {validation.scenarios.length}{' '}
          scenarios validated. Review individual evidence below.
        </Notice>
      )}
      <div className="scenario-workspace">
        <aside className="scenario-list">
          <div className="scenario-list-heading">
            SCENARIOS <span>{collection.scenarios.length}</span>
          </div>
          {collection.scenarios.map((s, i) => (
            <button
              key={s.id}
              className={selected === s.id ? 'selected' : ''}
              onClick={() => setSelected(s.id)}
            >
              <span className="scenario-number">{String(i + 1).padStart(2, '0')}</span>
              <div>
                <strong>{s.name}</strong>
                <span>
                  {domainLabel[s.domain]} · {s.checks.length} checks
                </span>
              </div>
              {validation?.scenarios.find((r) => r.id === s.id)?.valid && (
                <CheckCircle2 size={15} />
              )}
            </button>
          ))}
        </aside>
        {scenario ? (
          <section className="scenario-detail">
            <div className="scenario-detail-heading">
              <div>
                <DomainBadge domain={scenario.domain} />
                <h2>{scenario.name}</h2>
                {scenario.description && <p>{scenario.description}</p>}
              </div>
            </div>
            <div className="scenario-section">
              <h3>
                Task instruction <span className="pill">Agent-visible</span>
              </h3>
              <p className="preserve-lines">{scenario.instruction}</p>
              <JsonView value={scenario.inputs} label="Public inputs" open />
            </div>
            <div className="scenario-section">
              <h3>
                Evaluation checks <span className="pill amber">Evaluator only</span>
              </h3>
              <p className="help-text">
                These criteria stay outside model requests. Required checks must all pass.
              </p>
              <div className="check-list">
                {scenario.checks.map((c) => (
                  <div key={c.id} className="check-definition">
                    <div>
                      <ShieldCheck size={16} />
                      <strong>{c.name}</strong>
                      <span className="pill">{c.kind}</span>
                      {c.required && <span className="required-check">Required</span>}
                    </div>
                    <p>
                      Output path: <code>{c.path || '(whole output)'}</code> · Weight {c.weight}
                      {c.kind === 'numeric' && ` · Tolerance ${c.tolerance}`}
                    </p>
                    <JsonView value={c.expected} label="Expected value" />
                  </div>
                ))}
              </div>
            </div>
            <div className="scenario-section">
              <JsonView
                value={scenario.reference_output}
                label="Reference output · evaluator only"
              />
              <JsonView value={scenario.provenance} label="Provenance" />
              {scenario.tags.length > 0 && (
                <div className="tag-list">
                  {scenario.tags.map((t) => (
                    <span key={t}>{t}</span>
                  ))}
                </div>
              )}
            </div>
            {result && (
              <div className="scenario-section">
                <h3>Validation evidence</h3>
                {result.errors.map((e, i) => (
                  <Notice tone="error" key={i}>
                    {e}
                  </Notice>
                ))}
                <JsonView value={result.checks} label="Recorded validation checks" open />
              </div>
            )}
          </section>
        ) : (
          <EmptyState
            title="No scenarios yet"
            description="Import a collection with at least one scenario."
          />
        )}
      </div>
      <div className="collection-actions">
        <span>Experiments preserve a frozen snapshot of this collection.</span>
        <ConfirmDelete
          name="this collection"
          onConfirm={async () => {
            await request(`/collections/${encodeURIComponent(id)}`, { method: 'DELETE' });
            await refresh();
            window.location.hash = '/collections';
          }}
        />
      </div>
    </>
  );
}
const newDraft = () => ({
  name: '',
  instruction: '',
  inputs: '{}',
  reference: '{}',
  checkName: 'Expected output',
  path: '',
  kind: 'equals',
  expected: '',
  tolerance: '0',
});
export function NewCollection({ refresh }: { refresh: () => Promise<void> }) {
  const [mode, setMode] = useState<'build' | 'import' | 'repository'>('build');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [domain, setDomain] = useState<Domain>('documents');
  const [draft, setDraft] = useState(newDraft);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [source, setSource] = useState<unknown>(null);
  const [sourceName, setSourceName] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [importText, setImportText] = useState('');
  const importFile = useRef<HTMLInputElement>(null);
  const sourceFile = useRef<HTMLInputElement>(null);
  const hasDraft = useMemo(() => !!draft.name.trim() || !!draft.instruction.trim(), [draft]);
  const makeScenario = (): Scenario => {
    if (!draft.name.trim() || !draft.instruction.trim())
      throw new Error('Give the scenario a name and a task instruction.');
    const reference = parseObject(draft.reference, 'Reference output');
    const expected = draft.expected.trim() ? (JSON.parse(draft.expected) as unknown) : reference;
    return {
      id: crypto.randomUUID(),
      name: draft.name.trim(),
      domain,
      description: '',
      instruction: draft.instruction.trim(),
      inputs: parseObject(draft.inputs, 'Inputs'),
      expected: {},
      reference_output: reference,
      tags: [],
      provenance: {
        source: 'workspace authoring',
        ...(sourceName ? { filename: sourceName } : {}),
      },
      checks: [
        {
          id: crypto.randomUUID(),
          name: draft.checkName.trim() || 'Expected output',
          kind: draft.kind as Scenario['checks'][number]['kind'],
          path: draft.path.trim(),
          expected,
          tolerance: Number(draft.tolerance),
          weight: 1,
          required: true,
        },
      ],
    };
  };
  async function submitCollection() {
    setBusy(true);
    setError('');
    try {
      const all = hasDraft ? [...scenarios, makeScenario()] : scenarios;
      if (!all.length) throw new Error('Add at least one scenario before saving.');
      const collection = await post<Collection>('/collections', {
        name: name.trim(),
        description,
        domain: new Set(all.map((s) => s.domain)).size > 1 ? 'mixed' : all[0].domain,
        version: '1.0.0',
        scenarios: all,
      });
      await refresh();
      window.location.hash = `/collections/${encodeURIComponent(collection.id)}`;
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function importCollection(body: FormData | string) {
    setBusy(true);
    setError('');
    try {
      const collection = await request<Collection>('/import', { method: 'POST', body });
      await refresh();
      window.location.hash = `/collections/${encodeURIComponent(collection.id)}`;
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <a href="#/collections" className="back-link">
        <ArrowLeft size={15} />
        All collections
      </a>
      <PageHeader
        eyebrow="MAKE SUCCESS EXPLICIT"
        title="Create a collection"
        description="Bring the task, define the expected outcome, and make the evidence inspectable."
      />
      <div className="tabs" role="tablist" aria-label="Collection creation method">
        <button
          role="tab"
          aria-selected={mode === 'build'}
          onClick={() => {
            setMode('build');
            setError('');
          }}
        >
          <FolderPlus size={16} />
          Build a collection
        </button>
        <button
          role="tab"
          aria-selected={mode === 'import'}
          onClick={() => {
            setMode('import');
            setError('');
          }}
        >
          <Upload size={16} />
          Import JSON / YAML
        </button>
        <button
          role="tab"
          aria-selected={mode === 'repository'}
          onClick={() => {
            setMode('repository');
            setError('');
          }}
        >
          <FileText size={16} />
          Mine repository
        </button>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {mode === 'repository' ? (
        <RepositoryImport refresh={refresh} />
      ) : mode === 'import' ? (
        <div className="form-width">
          <Panel
            title="Import a task collection"
            detail="Use the versioned collection schema. Review imported tasks before running a provider."
          >
            <div className="panel-body">
              <input
                ref={importFile}
                type="file"
                accept=".json,.yaml,.yml"
                className="visually-hidden"
                aria-label="Collection file"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  if (file.size > 5 * 1024 * 1024) {
                    setError('Files must be smaller than 5 MB.');
                    return;
                  }
                  const body = new FormData();
                  body.append('file', file);
                  void importCollection(body);
                }}
              />
              <button
                className="upload-zone"
                disabled={busy}
                onClick={() => importFile.current?.click()}
              >
                <Upload size={25} />
                <strong>Choose a JSON or YAML file</strong>
                <span>Up to 5 MB · one collection per file</span>
              </button>
              <div className="form-divider">or paste collection JSON</div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  try {
                    const parsed = parseObject(importText, 'Collection');
                    void importCollection(JSON.stringify(parsed));
                  } catch (err) {
                    setError(errorMessage(err));
                  }
                }}
              >
                <Field label="Collection JSON">
                  <textarea
                    className="code-input"
                    rows={13}
                    required
                    placeholder={
                      '{\n  "name": "Policy review",\n  "domain": "documents",\n  "scenarios": [...]\n}'
                    }
                    value={importText}
                    onChange={(e) => setImportText(e.target.value)}
                  />
                </Field>
                <Button type="submit" busy={busy}>
                  <Upload size={16} />
                  Import collection
                </Button>
              </form>
            </div>
          </Panel>
        </div>
      ) : (
        <form
          className="authoring-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submitCollection();
          }}
        >
          <div className="authoring-main">
            <Panel
              title="Collection details"
              detail="Group scenarios that belong in the same evaluation."
            >
              <div className="panel-body">
                <Field label="Collection name" required>
                  <input
                    required
                    maxLength={160}
                    placeholder="e.g. Procurement policy review"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </Field>
                <div className="form-grid">
                  <Field label="Scenario domain">
                    <select value={domain} onChange={(e) => setDomain(e.target.value as Domain)}>
                      {Object.entries(domainLabel)
                        .filter(([key]) => key !== 'mixed')
                        .map(([key, label]) => (
                          <option value={key} key={key}>
                            {label}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Field label="Description">
                    <input
                      placeholder="What does this collection measure?"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                    />
                  </Field>
                </div>
              </div>
            </Panel>
            <Panel
              title={`Scenario ${scenarios.length + 1}`}
              detail="The task description and public inputs are visible to the system under evaluation."
            >
              <div className="panel-body">
                <input
                  ref={sourceFile}
                  type="file"
                  accept=".txt,.md,.csv,.tsv,.json,.jsonl,.yaml,.yml,.pdf,.docx"
                  className="visually-hidden"
                  aria-label="Source document"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    setUploading(true);
                    setError('');
                    try {
                      if (file.size > 5 * 1024 * 1024)
                        throw new Error('Files must be smaller than 5 MB.');
                      const body = new FormData();
                      body.append('file', file);
                      const preview = await request<Record<string, unknown>>('/sources', {
                        method: 'POST',
                        body,
                      });
                      setSource(preview);
                      setSourceName(file.name);
                      setDraft((d) => ({ ...d, inputs: json({ source: preview }) }));
                    } catch (err) {
                      setError(errorMessage(err));
                    } finally {
                      setUploading(false);
                    }
                  }}
                />
                <div className="source-bar">
                  <div>
                    <FileText size={18} />
                    <span>{sourceName || 'Start from a document or dataset'}</span>
                  </div>
                  <Button
                    type="button"
                    variant="secondary"
                    busy={uploading}
                    onClick={() => sourceFile.current?.click()}
                  >
                    <Upload size={15} />
                    {sourceName ? 'Replace source' : 'Preview source'}
                  </Button>
                </div>
                {source !== null && <JsonView value={source} label="Imported source preview" />}
                <Field label="Scenario name">
                  <input
                    maxLength={160}
                    placeholder="e.g. Identify invoices requiring approval"
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                </Field>
                <Field
                  label="Task instruction"
                  hint="Specify the desired output format. Do not include the reference answer."
                >
                  <textarea
                    rows={4}
                    placeholder="Review the supplied invoices against the approval policy. Return a JSON object with…"
                    value={draft.instruction}
                    onChange={(e) => setDraft({ ...draft, instruction: e.target.value })}
                  />
                </Field>
                <Field
                  label="Public inputs · JSON object"
                  hint="These inputs are included in provider requests."
                >
                  <textarea
                    className="code-input"
                    rows={5}
                    value={draft.inputs}
                    onChange={(e) => setDraft({ ...draft, inputs: e.target.value })}
                  />
                </Field>
              </div>
            </Panel>
            <Panel
              title="Define success"
              detail="Evaluator-only material. This stays outside model requests."
            >
              <div className="panel-body">
                <Field
                  label="Reference output · JSON object"
                  hint="An accepted answer for validation and the reference fixture control."
                >
                  <textarea
                    className="code-input"
                    rows={5}
                    value={draft.reference}
                    onChange={(e) => setDraft({ ...draft, reference: e.target.value })}
                  />
                </Field>
                <div className="form-grid">
                  <Field label="Check name">
                    <input
                      value={draft.checkName}
                      onChange={(e) => setDraft({ ...draft, checkName: e.target.value })}
                    />
                  </Field>
                  <Field label="Check type">
                    <select
                      value={draft.kind}
                      onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
                    >
                      <option value="equals">Exact equality</option>
                      <option value="contains">Contains expected value</option>
                      <option value="numeric">Numeric tolerance</option>
                      <option value="forbidden">Forbidden content absent</option>
                      <option value="json_schema">JSON schema</option>
                    </select>
                  </Field>
                </div>
                <div className="form-grid">
                  <Field
                    label="Output path"
                    hint="Leave blank for the whole output; dotted keys, e.g. totals.tax."
                  >
                    <input
                      placeholder="(whole output)"
                      value={draft.path}
                      onChange={(e) => setDraft({ ...draft, path: e.target.value })}
                    />
                  </Field>
                  {draft.kind === 'numeric' ? (
                    <Field label="Tolerance">
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={draft.tolerance}
                        onChange={(e) => setDraft({ ...draft, tolerance: e.target.value })}
                      />
                    </Field>
                  ) : (
                    <div />
                  )}
                </div>
                <Field
                  label="Expected value · JSON"
                  hint="Leave blank to compare against the complete reference output. Strings need double quotes."
                >
                  <textarea
                    className="code-input"
                    rows={3}
                    placeholder="Use reference output"
                    value={draft.expected}
                    onChange={(e) => setDraft({ ...draft, expected: e.target.value })}
                  />
                </Field>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    try {
                      setScenarios([...scenarios, makeScenario()]);
                      setDraft(newDraft());
                      setSource(null);
                      setSourceName('');
                      setError('');
                    } catch (e) {
                      setError(errorMessage(e));
                    }
                  }}
                >
                  <Plus size={16} />
                  Save scenario & add another
                </Button>
              </div>
            </Panel>
            <div className="form-footer">
              <LinkButton href="#/collections" secondary>
                Cancel
              </LinkButton>
              <Button type="submit" busy={busy}>
                <Check size={16} />
                Create collection
              </Button>
            </div>
          </div>
          <aside className="authoring-aside">
            <div className="authoring-note">
              <ShieldCheck size={23} />
              <h3>Good evaluations start with clear criteria.</h3>
              <p>
                Use representative work, a precise objective, and checks that accept every valid
                solution.
              </p>
              <hr />
              <h4>Your collection</h4>
              <div className="draft-count">
                {scenarios.length + (hasDraft ? 1 : 0)} <span>scenarios</span>
              </div>
              {scenarios.map((s, i) => (
                <div className="draft-scenario" key={s.id}>
                  <CheckCircle2 size={15} />
                  <span>{s.name}</span>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={`Remove ${s.name}`}
                    onClick={() => setScenarios(scenarios.filter((_, j) => j !== i))}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
              <hr />
              <p>
                Need multiple checks, citations, or sandboxed Python tests? Import a JSON or YAML
                collection with the complete schema.
              </p>
            </div>
          </aside>
        </form>
      )}
    </>
  );
}
