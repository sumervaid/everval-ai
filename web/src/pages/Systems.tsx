import { useState } from 'react';
import { Beaker, CheckCircle2, KeyRound, Plus, Server, SlidersHorizontal, X } from 'lucide-react';
import { post, request } from '../api';
import type { System, Workspace } from '../types';
import { errorMessage } from '../format';
import { Button, ConfirmDelete, EmptyState, Field, Notice, PageHeader, Panel } from '../components';
const initialForm = {
  name: '',
  kind: 'http' as System['kind'],
  model: '',
  base_url: '',
  api_key_env: '',
  temperature: '',
  token_limit_field: 'max_completion_tokens',
  max_tokens: '4096',
  input_cost_per_million: '',
  output_cost_per_million: '',
};
export function SystemsPage({
  workspace,
  refresh,
}: {
  workspace: Workspace;
  refresh: () => Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState('');
  async function save() {
    setBusy(true);
    setError('');
    try {
      await post('/systems', {
        ...form,
        temperature: form.temperature === '' ? null : Number(form.temperature),
        max_tokens: Number(form.max_tokens),
        input_cost_per_million:
          form.input_cost_per_million === '' ? null : Number(form.input_cost_per_million),
        output_cost_per_million:
          form.output_cost_per_million === '' ? null : Number(form.output_cost_per_million),
      });
      setSaved(`${form.name} was added.`);
      setShowForm(false);
      setForm(initialForm);
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const providers = workspace.systems.filter((s) => s.kind === 'http');
  const fixtures = workspace.systems.filter(
    (s) => s.kind === 'reference' || s.kind === 'incomplete',
  );
  return (
    <>
      <PageHeader
        eyebrow="CONFIGURE THE CANDIDATES"
        title="Systems"
        description="Versioned model configurations and explicit controls for your experiments."
        actions={
          <Button
            onClick={() => {
              setShowForm(true);
              setSaved('');
            }}
          >
            <Plus size={16} />
            Add system
          </Button>
        }
      />
      {saved && <Notice tone="success">{saved}</Notice>}
      {showForm && (
        <Panel
          title="Add a system"
          detail="Existing experiment snapshots stay unchanged when systems are added or removed."
          action={
            <button
              className="icon-button"
              aria-label="Close system form"
              onClick={() => setShowForm(false)}
            >
              <X size={18} />
            </button>
          }
        >
          <form
            className="panel-body"
            onSubmit={(e) => {
              e.preventDefault();
              void save();
            }}
          >
            {error && <Notice tone="error">{error}</Notice>}
            <div className="form-grid">
              <Field label="Name" required>
                <input
                  autoFocus
                  required
                  maxLength={160}
                  placeholder="e.g. Local model · balanced"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </Field>
              <Field label="Adapter">
                <select
                  value={form.kind}
                  onChange={(e) => setForm({ ...form, kind: e.target.value as System['kind'] })}
                >
                  <option value="http">Compatible chat-completions endpoint</option>
                  <option value="reference">Reference fixture control</option>
                  <option value="incomplete">Incomplete fixture control</option>
                </select>
              </Field>
            </div>
            {form.kind === 'http' ? (
              <>
                <div className="form-grid">
                  <Field
                    label="Base URL"
                    required
                    hint="API base ending in /v1, for example http://localhost:11434/v1."
                  >
                    <input
                      required
                      type="url"
                      placeholder="http://localhost:11434/v1"
                      value={form.base_url}
                      onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    />
                  </Field>
                  <Field label="Model identifier" required>
                    <input
                      required
                      placeholder="Your provider’s exact model ID"
                      value={form.model}
                      onChange={(e) => setForm({ ...form, model: e.target.value })}
                    />
                  </Field>
                </div>
                <Field
                  label="API key environment variable"
                  hint="Variable name only. Set its value in the service environment, then restart the service."
                >
                  <div className="input-with-icon">
                    <KeyRound size={16} />
                    <input
                      pattern="[A-Z_][A-Z0-9_]*"
                      placeholder="e.g. CUSTOM_VALS_PROVIDER_KEY"
                      value={form.api_key_env}
                      onChange={(e) => setForm({ ...form, api_key_env: e.target.value })}
                    />
                  </div>
                </Field>
                <div className="form-grid four">
                  <Field label="Temperature" hint="Leave blank to use the provider default.">
                    <input
                      type="number"
                      min="0"
                      max="2"
                      step="0.1"
                      value={form.temperature}
                      onChange={(e) => setForm({ ...form, temperature: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Token limit parameter"
                    hint="Match your endpoint’s chat-completions API."
                  >
                    <select
                      value={form.token_limit_field}
                      onChange={(e) => setForm({ ...form, token_limit_field: e.target.value })}
                    >
                      <option value="max_completion_tokens">max_completion_tokens</option>
                      <option value="max_tokens">max_tokens</option>
                    </select>
                  </Field>
                  <Field label="Maximum output tokens">
                    <input
                      required
                      type="number"
                      min="1"
                      max="32768"
                      value={form.max_tokens}
                      onChange={(e) => setForm({ ...form, max_tokens: e.target.value })}
                    />
                  </Field>
                  <Field
                    label="Input USD / 1M tokens"
                    hint="Use 0 for an explicitly free local model."
                  >
                    <input
                      type="number"
                      min="0"
                      step="any"
                      placeholder="Not configured"
                      value={form.input_cost_per_million}
                      onChange={(e) => setForm({ ...form, input_cost_per_million: e.target.value })}
                    />
                  </Field>
                  <Field label="Output USD / 1M tokens">
                    <input
                      type="number"
                      min="0"
                      step="any"
                      placeholder="Not configured"
                      value={form.output_cost_per_million}
                      onChange={(e) =>
                        setForm({ ...form, output_cost_per_million: e.target.value })
                      }
                    />
                  </Field>
                </div>
                <Notice>
                  Each request contains the public scenario instruction and inputs, and asks for a
                  JSON object. Confirm your endpoint supports this response format. Remote endpoints
                  require service opt-in, pricing, and a positive experiment budget.
                </Notice>
              </>
            ) : (
              <Notice tone="warning">
                <strong>Fixture control · not a model.</strong>{' '}
                {form.kind === 'reference'
                  ? 'Replays each scenario’s stored reference output to exercise grading.'
                  : 'Submits an incomplete answer to check whether evaluators detect missing work.'}{' '}
                Fixture results do not measure model capability.
              </Notice>
            )}
            <div className="form-footer">
              <Button type="button" variant="secondary" onClick={() => setShowForm(false)}>
                Cancel
              </Button>
              <Button type="submit" busy={busy}>
                <Plus size={16} />
                Save system
              </Button>
            </div>
          </form>
        </Panel>
      )}
      <Panel title="Model endpoints" detail="The configurations you intend to evaluate.">
        {providers.length ? (
          <div className="system-list">
            {providers.map((s) => (
              <SystemRow key={s.id} system={s} refresh={refresh} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="Connect a model to begin comparing."
            description="Use a local model or a compatible provider endpoint. Credentials remain in the service environment."
            action={
              <Button variant="secondary" onClick={() => setShowForm(true)}>
                <Server size={16} />
                Add model endpoint
              </Button>
            }
          />
        )}
      </Panel>
      <Panel
        title="Fixture controls"
        detail="Deterministic checks of the evaluation pipeline. Never model-performance evidence."
      >
        {fixtures.length ? (
          <div className="system-list">
            {fixtures.map((s) => (
              <SystemRow key={s.id} system={s} refresh={refresh} />
            ))}
          </div>
        ) : (
          <EmptyState
            compact
            title="No fixture controls"
            description="Install the starter collections from Overview, or add a reference or incomplete fixture control."
          />
        )}
      </Panel>
      {workspace.systems.some((s) => s.kind === 'replay') && (
        <Panel
          title="Imported submissions"
          detail="Externally produced outputs, graded with the same evaluation checks."
        >
          <div className="system-list">
            {workspace.systems
              .filter((s) => s.kind === 'replay')
              .map((s) => (
                <div className="system-row" key={s.id}>
                  <div className="system-icon">
                    <Server size={22} />
                  </div>
                  <div className="system-info">
                    <h3>
                      {s.name}
                      <span className="pill">Imported submissions</span>
                    </h3>
                    <p>Evidence supplied by an external evaluation run.</p>
                  </div>
                </div>
              ))}
          </div>
        </Panel>
      )}
      <div className="principles-strip">
        <SlidersHorizontal size={18} />
        <strong>Configuration is part of the evidence.</strong>
        <span>Experiments freeze model, adapter, pricing, and generation settings.</span>
      </div>
    </>
  );
}
function SystemRow({ system: s, refresh }: { system: System; refresh: () => Promise<void> }) {
  const fixture = s.kind === 'reference' || s.kind === 'incomplete';
  return (
    <div className="system-row">
      <div className={`system-icon ${fixture ? 'fixture' : ''}`}>
        {fixture ? <Beaker size={22} /> : <Server size={22} />}
      </div>
      <div className="system-info">
        <h3>
          {s.name}
          <span className={`pill ${fixture ? 'amber' : ''}`}>
            {fixture ? 'Fixture control' : 'Model endpoint'}
          </span>
        </h3>
        <p>
          {fixture
            ? s.kind === 'reference'
              ? 'Stored reference answers · deterministic positive control'
              : 'Incomplete outputs · negative control'
            : `${s.model} · ${s.base_url}`}
        </p>
        <div className="system-config">
          {fixture ? (
            <span>No provider requests</span>
          ) : (
            <>
              <span>Temperature {s.temperature ?? 'provider default'}</span>
              <span>{s.max_tokens.toLocaleString()} max tokens</span>
              <span>{s.api_key_env ? `Key: ${s.api_key_env}` : 'No key configured'}</span>
            </>
          )}
        </div>
      </div>
      <div className="system-row-actions">
        {!fixture && (
          <span className={`configuration-state ${s.configured ? 'ready' : ''}`}>
            {s.configured ? <CheckCircle2 size={15} /> : <KeyRound size={15} />}{' '}
            {s.configured ? 'Configured' : 'Check environment'}
          </span>
        )}
        <ConfirmDelete
          name={s.name}
          onConfirm={async () => {
            await request(`/systems/${encodeURIComponent(s.id)}`, { method: 'DELETE' });
            await refresh();
          }}
        />
      </div>
    </div>
  );
}
