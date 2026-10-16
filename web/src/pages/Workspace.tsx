import { useState } from 'react';
import {
  ArrowRight,
  Check,
  Code2,
  Database,
  FileText,
  FlaskConical,
  FolderPlus,
  GitBranch,
  Layers3,
  Plus,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import { post, setToken } from '../api';
import { bytes, errorMessage, money, percent } from '../format';
import type { Workspace } from '../types';
import { Button, LinkButton, Notice, PageHeader, Panel, TextLink } from '../components';
import { SchedulesPanel } from './Schedules';
import { ExperimentTable } from './Experiments';

export function StarterButton({
  refresh,
  secondary = false,
}: {
  refresh: () => Promise<void>;
  secondary?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <div className="starter-action">
      <Button
        variant={secondary ? 'secondary' : 'primary'}
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError('');
          try {
            await post('/starters');
            await refresh();
          } catch (e) {
            setError(errorMessage(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Layers3 size={16} />
        Install starter collections
      </Button>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
export function Dashboard({
  workspace,
  refresh,
}: {
  workspace: Workspace;
  refresh: () => Promise<void>;
}) {
  const { overview, collections, systems, experiments } = workspace;
  const hasProvider = systems.some((s) => s.kind === 'http');
  const domains = [
    {
      domain: 'documents',
      title: 'Documents',
      icon: FileText,
      text: 'Claims, citations, and complete answers.',
    },
    {
      domain: 'data',
      title: 'Structured data',
      icon: Database,
      text: 'Exact values, schemas, and calculations.',
    },
    {
      domain: 'workflow',
      title: 'Workflows',
      icon: GitBranch,
      text: 'Correct outcomes and policy constraints.',
    },
    {
      domain: 'coding',
      title: 'Code',
      icon: Code2,
      text: 'Functional correctness in isolated tests.',
    },
  ];
  return (
    <>
      <PageHeader
        eyebrow="YOUR EVALUATION WORKSPACE"
        title="Make the work the benchmark."
        description="Build meaningful scenarios. Compare systems. Follow the evidence."
        actions={
          <LinkButton href="#/experiments/new">
            <Plus size={16} />
            New experiment
          </LinkButton>
        }
      />
      <div className="stats-grid">
        <div className="stat-card">
          <span>
            Collections
            <Layers3 size={16} />
          </span>
          <strong>{overview.collections}</strong>
          <p>{overview.scenarios} scenarios in your workspace</p>
        </div>
        <div className="stat-card">
          <span>
            Experiments
            <FlaskConical size={16} />
          </span>
          <strong>{overview.experiments}</strong>
          <p>{overview.completed_experiments} completed experiments</p>
        </div>
        <div className="stat-card">
          <span>
            System pass rate
            <ShieldCheck size={16} />
          </span>
          <strong>{percent(overview.pass_rate)}</strong>
          <p>
            {overview.pass_rate == null
              ? 'No system evidence yet'
              : 'Excludes fixture-control trials'}
          </p>
        </div>
        <div className="stat-card">
          <span>
            Recorded cost
            <Database size={16} />
          </span>
          <strong>{money(overview.total_cost_usd)}</strong>
          <p>USD · missing costs stay unknown</p>
        </div>
      </div>
      {(!collections.length || !hasProvider) && (
        <div className="onboarding-panel">
          <div className="onboarding-intro">
            <div className="eyebrow">A CLEAR STARTING POINT</div>
            <h2>
              Your first evaluation,
              <br /> from input to insight.
            </h2>
            <p>
              Start with original example scenarios, then shape a collection around your
              organization’s work.
            </p>
            {!collections.length ? (
              <StarterButton refresh={refresh} />
            ) : (
              <LinkButton href="#/experiments/new">
                <FlaskConical size={16} />
                Run your first experiment
              </LinkButton>
            )}
            <span className="small-note">
              Starter controls verify the pipeline. They are not model benchmarks.
            </span>
          </div>
          <ol className="steps">
            <li className={collections.length ? 'done' : ''}>
              <span>{collections.length ? <Check size={15} /> : '01'}</span>
              <div>
                <h3>Define the work</h3>
                <p>Bring your documents, records, or code. Specify what success means.</p>
                <TextLink href="#/collections/new">Create a collection</TextLink>
              </div>
            </li>
            <li className={hasProvider ? 'done' : ''}>
              <span>{hasProvider ? <Check size={15} /> : '02'}</span>
              <div>
                <h3>Choose your systems</h3>
                <p>Connect a compatible model endpoint, or begin with offline fixture controls.</p>
                <TextLink href="#/systems">Configure systems</TextLink>
              </div>
            </li>
            <li className={experiments.length ? 'done' : ''}>
              <span>{experiments.length ? <Check size={15} /> : '03'}</span>
              <div>
                <h3>Inspect the evidence</h3>
                <p>Run the same tasks, inspect every check, and compare uncertainty and cost.</p>
                <TextLink href="#/experiments/new">Design an experiment</TextLink>
              </div>
            </li>
          </ol>
        </div>
      )}
      <Panel
        title="Recent experiments"
        detail="Every attempt remains part of the record."
        action={<TextLink href="#/experiments">View all experiments</TextLink>}
      >
        <ExperimentTable
          experiments={overview.recent_experiments ?? experiments.slice(0, 5)}
          emptyAction={
            <LinkButton href="#/experiments/new" secondary>
              <Plus size={15} />
              Create experiment
            </LinkButton>
          }
        />
      </Panel>
      <div className="section-label">
        <div>
          <h2>One framework. Different kinds of work.</h2>
          <p>Each domain uses checks appropriate to the task.</p>
        </div>
        <TextLink href="#/collections">Explore collections</TextLink>
      </div>
      <div className="domain-grid">
        {domains.map((d) => (
          <a key={d.domain} href="#/collections" className="domain-card">
            <d.icon size={21} />
            <h3>
              {d.title}
              <ArrowRight size={15} />
            </h3>
            <p>{d.text}</p>
            <span>
              {collections
                .filter((c) => c.domain === d.domain)
                .reduce((n, c) => n + c.scenarios.length, 0)}{' '}
              scenarios
            </span>
          </a>
        ))}
      </div>
      <div className="principles-strip">
        <ShieldCheck size={18} />
        <strong>Evidence before rankings.</strong>
        <span>Preserved failures. Visible uncertainty. Reproducible reports.</span>
      </div>
    </>
  );
}
export function SettingsPage({ workspace }: { workspace: Workspace }) {
  const s = workspace.settings;
  const [cleared, setCleared] = useState(false);
  return (
    <>
      <PageHeader
        eyebrow="LOCAL BY DESIGN"
        title="Settings & setup"
        description="Your service, storage, and execution capabilities in one place."
      />
      <div className="two-column">
        <Panel title="Workspace" detail="Read from the running service.">
          <dl className="settings-list">
            <div>
              <dt>Application</dt>
              <dd>Everval AI {s.version}</dd>
            </div>
            <div>
              <dt>Python</dt>
              <dd>{s.python_version}</dd>
            </div>
            <div>
              <dt>Available storage</dt>
              <dd>{bytes(s.storage_free_bytes)}</dd>
            </div>
            <div>
              <dt>Data directory</dt>
              <dd>
                <code>{s.data_dir}</code>
              </dd>
            </div>
            <div>
              <dt>Worker</dt>
              <dd>
                <span className={s.worker_running ? 'text-green' : 'text-muted'}>
                  {s.worker_running ? 'Running' : 'Not running'}
                </span>
              </dd>
            </div>
            <div>
              <dt>Workspace authentication</dt>
              <dd>{s.auth_enabled ? 'Token required' : 'Loopback access'}</dd>
            </div>
          </dl>
          {s.auth_enabled && (
            <div className="panel-body">
              <Button
                variant="secondary"
                onClick={() => {
                  setToken('');
                  setCleared(true);
                }}
              >
                Forget session token
              </Button>
              {cleared && (
                <p className="small-note">
                  Session token removed. Reconnect to access the workspace.
                </p>
              )}
            </div>
          )}
        </Panel>
        <Panel
          title="Execution capabilities"
          detail="Unavailable capabilities fail with an explicit reason."
        >
          <div className="capability">
            <div className="capability-icon">
              <Code2 size={19} />
            </div>
            <div>
              <h3>Isolated Python grading</h3>
              <p>
                {s.docker_available
                  ? 'Docker is available. Images and sandbox policies are checked when grading.'
                  : 'Docker is unavailable. Python execution checks cannot run; other checks remain available.'}
              </p>
            </div>
            <span className={`pill ${s.docker_available ? 'green' : 'amber'}`}>
              {s.docker_available ? 'Available' : 'Needs setup'}
            </span>
          </div>
          <div className="capability">
            <div className="capability-icon">
              <GitBranch size={19} />
            </div>
            <div>
              <h3>Public model endpoints</h3>
              <p>
                {s.remote_providers_enabled
                  ? 'Enabled. Provider runs still require configured pricing and a budget.'
                  : 'Disabled. Internal inference uses its own explicit origin policy.'}
              </p>
            </div>
            <span className={`pill ${s.remote_providers_enabled ? 'green' : ''}`}>
              {s.remote_providers_enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
          <div className="capability">
            <div className="capability-icon">
              <ShieldCheck size={19} />
            </div>
            <div>
              <h3>Internal inference</h3>
              <p>
                {s.internal_provider_origins?.length
                  ? s.internal_provider_origins.join(', ')
                  : 'No internal HTTPS origins approved. Configure them on the service host.'}
              </p>
              <p>
                TLS verification required.
                {s.provider_ca_configured
                  ? ' An additional CA bundle is configured.'
                  : ' Standard certificate trust applies.'}
              </p>
            </div>
            <span className={`pill ${s.internal_provider_origins?.length ? 'green' : ''}`}>
              {s.internal_provider_origins?.length ?? 0} approved
            </span>
          </div>
          <div className="capability">
            <div className="capability-icon">
              <ShieldCheck size={19} />
            </div>
            <div>
              <h3>Local records & artifacts</h3>
              <p>
                Collections, snapshots, trial evidence, and reports stay in your configured data
                directory.
              </p>
            </div>
            <span className="pill green">Active</span>
          </div>
        </Panel>
      </div>
      <SchedulesPanel />
      <Panel
        title="A practical first run"
        detail="The same evaluation core powers the app and command line."
      >
        <div className="guide-grid">
          <div>
            <FolderPlus size={22} />
            <h3>1. Start with a collection</h3>
            <p>
              Install the starter collections to explore the schema, or import your own JSON or YAML
              task collection.
            </p>
            <TextLink href="#/collections">Open collections</TextLink>
          </div>
          <div>
            <Settings2 size={22} />
            <h3>2. Establish a control</h3>
            <p>
              Reference and incomplete fixture controls exercise the checks without calling a model.
              Expect different outcomes.
            </p>
            <TextLink href="#/systems">Review systems</TextLink>
          </div>
          <div>
            <FlaskConical size={22} />
            <h3>3. Compare on fixed tasks</h3>
            <p>
              Select a collection and systems. Add repetitions and a spending ceiling. Review trial
              evidence before drawing conclusions.
            </p>
            <TextLink href="#/experiments/new">Create experiment</TextLink>
          </div>
        </div>
      </Panel>
      <Notice>
        <strong>Local storage and local inference are separate.</strong> A configured remote
        endpoint receives scenario instructions and public inputs. Evaluator criteria and reference
        answers are excluded from provider requests. API credentials are read from service
        environment variables.
      </Notice>
      <Panel
        title="Command-line companion"
        detail="Use the CLI for scripts, repeatable runs, and service diagnostics."
      >
        <div className="terminal-block">
          <span>$</span> everval --help
          <br />
          <span>$</span> everval doctor
          <br />
          <span>$</span> everval serve
        </div>
        <div className="panel-body">
          <a
            className="text-link"
            href="https://github.com/sumervaid/everval-ai#readme"
            target="_blank"
            rel="noreferrer"
          >
            Read the setup guide
            <ArrowRight size={15} />
          </a>
        </div>
      </Panel>
    </>
  );
}
