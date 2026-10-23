import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type { TrainingDashboard, TrainingMetric } from '../training-types';
import { TrainingPage, trainingLag } from './Training';

function metric(extra: Partial<TrainingMetric> = {}): TrainingMetric {
  return {
    domain: 'Reasoning',
    experiment_id: 'saved/run',
    status: 'completed',
    value: 0,
    lower: 0,
    upper: 0.3,
    interval_method: 'Descriptive family interval',
    n: 10,
    expected_n: 10,
    known_cost_usd: 0,
    cost_complete: true,
    unknown_cost_count: 0,
    errors: 0,
    missing: 0,
    lag_seconds: 0,
    ...extra,
  };
}
function report(id = 'run-a'): TrainingDashboard {
  return {
    schema_version: 1,
    generated_at: '2026-10-22T12:00:00Z',
    run: {
      id,
      name: 'Synthetic UI control',
      training_run_id: id,
      provenance_kind: 'synthetic_control',
      created_at: '2026-10-22T12:00:00Z',
      checkpoint_count: 3,
      status: 'registered',
      protocol: {
        domains: ['Reasoning', 'Safety'].map((name) => ({
          name,
          collection_id: name,
          task_families: {},
          practical_threshold: 0.05,
        })),
        baseline_step: 0,
        comparison_steps: [100, 200],
        max_looks: 2,
        alpha: 0.05,
        min_clusters: 5,
        repetitions: 1,
        budget_per_domain_usd: 0,
        lifetime_budget_usd: 0,
      },
    },
    checkpoints: [200, 0, 100].map((step) => ({
      id: id + '-' + step,
      step,
      weight_sha256: 'a'.repeat(64),
      system_id: 'control',
      system_hash: 'b'.repeat(64),
      created_at: '2026-10-22T12:00:00Z',
      status: step === 100 ? 'running' : 'completed',
      metrics: [
        step === 100
          ? metric({
              value: null,
              lower: null,
              upper: null,
              n: 4,
              status: 'running',
              missing: 6,
              unknown_cost_count: 6,
              cost_complete: false,
            })
          : metric(),
      ],
      known_cost_usd: 0,
      unknown_cost_count: step === 100 ? 6 : 0,
      lag_seconds: step === 100 ? 75 : 0,
    })),
    comparisons: [
      {
        id: 'comparison',
        baseline_step: 0,
        candidate_step: 200,
        look_index: 2,
        max_looks: 2,
        allocated_alpha: 0.025,
        stale: true,
        synthetic_control: true,
        domains: [
          {
            domain: 'Reasoning',
            decision: 'inconclusive',
            valid: true,
            reasons: ['Too few task families to resolve a practical difference.'],
            expected_pairs: 10,
            matched_pairs: 10,
            clusters: 5,
            interval: [-1, 1],
            delta: 0,
            comparison_alpha: 0.0125,
            method: 'hoeffding',
            execution_contract_issues: [],
          },
        ],
      },
    ],
    alerts: [],
    investigations: [
      {
        id: 'investigation',
        observations: 'Grader revision changed.',
        hypotheses: ['Scorer drift'],
        recommendation: 'Regrade frozen outputs under both graders.',
        next_checks: ['Compare adjudications'],
        attribution: 'Hypotheses are author judgments; causality is not established.',
      },
    ],
    qualifications: [],
    mix_studies: [],
    mix_validations: [],
    budget: {
      allocated_usd: 0,
      lifetime_budget_usd: 0,
      known_cost_usd: 0,
      unknown_cost_count: 6,
    },
  };
}
function respond(data = report()) {
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      Promise.resolve(
        new Response(JSON.stringify(url === '/api/training/runs' ? [data.run] : data)),
      ),
    );
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = '';
});
describe('training checkpoint observability', () => {
  it('sorts steps, separates absent scores from zero, and does not join across missing evidence', async () => {
    const fetcher = respond();
    render(<TrainingPage />);
    const table = await screen.findByRole('table', {
      name: 'Checkpoint coverage by evaluation domain',
    });
    const rows = within(table).getAllByRole('row');
    expect(rows.slice(1).map((row) => within(row).getByRole('button').textContent)).toEqual([
      'Inspect step 0',
      'Inspect step 100',
      'Inspect step 200',
    ]);
    expect(within(rows[1]).getByText('0.0%')).toBeTruthy();
    expect(within(rows[2]).getAllByText('No score')).toHaveLength(2);
    expect(within(rows[2]).getByText('4/10 graded')).toBeTruthy();
    expect(within(rows[2]).getByText('0 errors · 6 missing')).toBeTruthy();
    expect(within(rows[2]).getByText('≥ $0.00')).toBeTruthy();
    const chart = screen.getByRole('img', {
      name: 'Reasoning: equal-family pass rate across training steps',
    });
    expect(chart.querySelectorAll('circle')).toHaveLength(2);
    expect(chart.querySelectorAll('.training-connection')).toHaveLength(0);
    expect(chart.querySelectorAll('.training-gap')).toHaveLength(1);
    expect(screen.getAllByText(/Synthetic control — not model performance/).length).toBeGreaterThan(
      0,
    );
    expect(fetcher.mock.calls.every((call) => !call[1].method)).toBe(true);
  });
  it('selects exact checkpoint evidence, descriptive intervals and encoded source links', async () => {
    respond();
    render(<TrainingPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect step 0' }));
    expect(screen.getByRole('heading', { name: 'Checkpoint evidence · step 0' })).toBeTruthy();
    expect(screen.getByText(/Reported interval 0.0%–30.0%/)).toBeTruthy();
    expect(
      screen.getByRole('link', { name: 'Open Reasoning experiment' }).getAttribute('href'),
    ).toBe('#/experiments/saved%2Frun');
    fireEvent.change(screen.getByLabelText('Evaluation domain'), {
      target: { value: 'Safety' },
    });
    const chart = screen.getByRole('img', {
      name: 'Safety: equal-family pass rate across training steps',
    });
    expect(chart.querySelectorAll('circle')).toHaveLength(0);
    expect(chart.querySelectorAll('.training-gap')).toHaveLength(3);
  });
  it('shows stale comparisons and uncertainty rather than a false clean bill of health', async () => {
    respond();
    render(<TrainingPage />);
    await screen.findByRole('table');
    expect(screen.getByText(/Stale comparison: source evidence changed/)).toBeTruthy();
    expect(screen.getByText('stale evidence')).toBeTruthy();
    expect(
      screen.getByText('Too few task families to resolve a practical difference.'),
    ).toBeTruthy();
    expect(screen.getByText(/10\/10 matched pairs · 5 task families/)).toBeTruthy();
    expect(screen.getByText('Regrade frozen outputs under both graders.')).toBeTruthy();
    expect(screen.getByText('Scorer drift')).toBeTruthy();
  });
  it('retains selection during refresh and marks retained evidence when refresh fails', async () => {
    const fetcher = respond();
    render(<TrainingPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect step 0' }));
    fetcher.mockImplementation((url: string) =>
      Promise.resolve(
        url === '/api/training/runs'
          ? new Response(JSON.stringify([report().run]))
          : new Response(JSON.stringify({ detail: 'Store offline' }), { status: 503 }),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh training evidence' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      'Showing the last successful snapshot',
    );
    expect(screen.getByRole('heading', { name: 'Checkpoint evidence · step 0' })).toBeTruthy();
    respond();
  });
  it('loads the chosen run and does not retain another run’s evidence', async () => {
    const data = report();
    const second = report('run-b');
    second.run.name = 'Second run';
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation((url: string) =>
          Promise.resolve(
            new Response(
              JSON.stringify(
                url === '/api/training/runs'
                  ? [data.run, second.run]
                  : url.endsWith('/run-b')
                    ? second
                    : data,
              ),
            ),
          ),
        ),
    );
    render(<TrainingPage />);
    await screen.findByRole('table');
    fireEvent.change(screen.getByLabelText('Training run'), { target: { value: 'run-b' } });
    await waitFor(() => expect(screen.getByText(/Training identity: run-b/)).toBeTruthy());
    expect(screen.queryByText(/Training identity: run-a/)).toBeNull();
  });
  it('explains an empty workspace without inserting demonstration evidence', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('[]')));
    render(<TrainingPage />);
    expect(await screen.findByText('Start with a frozen training protocol')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });
  it('exposes navigation to authorized operators and its own title', async () => {
    window.location.hash = '#/training';
    vi.stubGlobal('scrollTo', vi.fn());
    const data: Record<string, unknown> = {
      '/api/session': {
        id: 'operator',
        name: 'Operator',
        mode: 'local',
        role: 'operator',
        capabilities: ['workspace.read', 'workspace.write'],
      },
      '/api/overview': {},
      '/api/collections': [],
      '/api/systems': [],
      '/api/experiments': [],
      '/api/settings': { version: '0.3.1' },
      '/api/training/runs': [],
    };
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation((url: string) =>
          Promise.resolve(new Response(JSON.stringify(data[url]))),
        ),
    );
    render(<App />);
    await screen.findByRole('heading', { name: 'Training evaluations' });
    expect(screen.getByRole('link', { name: 'Training' }).getAttribute('aria-current')).toBe(
      'page',
    );
    await waitFor(() => expect(document.title).toBe('Training · Everval AI'));
  });
  it('keeps unknown and zero lag distinct', () => {
    expect(trainingLag(null)).toBe('Unknown');
    expect(trainingLag(0)).toBe('0 s');
    expect(trainingLag(75)).toBe('1.3 min');
  });
});

describe('training access and asynchronous boundaries', () => {
  it('does not read private training evidence for a viewer without workspace access', async () => {
    window.location.hash = '#/training';
    vi.stubGlobal('scrollTo', vi.fn());
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'viewer',
          name: 'Viewer',
          mode: 'team',
          role: 'viewer',
          capabilities: ['summary.read'],
        }),
      ),
    );
    vi.stubGlobal('fetch', fetcher);
    render(<App />);
    expect(await screen.findByText(/This page is unavailable for your role/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Training' })).toBeNull();
    expect(fetcher.mock.calls.every((call) => call[0] === '/api/session')).toBe(true);
  });

  it('discards a late response from a previously selected training run', async () => {
    const first = report();
    const second = report('run-b');
    let finishFirst: (response: Response) => void = () => {
      throw new Error('First request not started');
    };
    const late = new Promise<Response>((resolve) => {
      finishFirst = resolve;
    });
    const fetcher = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/training/runs')
        return Promise.resolve(new Response(JSON.stringify([first.run, second.run])));
      if (url.endsWith('/run-a')) return late;
      return Promise.resolve(new Response(JSON.stringify(second)));
    });
    vi.stubGlobal('fetch', fetcher);
    render(<TrainingPage />);
    const selector = await screen.findByLabelText('Training run');
    await waitFor(() =>
      expect(fetcher.mock.calls.some((call) => call[0].endsWith('/run-a'))).toBe(true),
    );
    fireEvent.change(selector, { target: { value: 'run-b' } });
    await screen.findByRole('table');
    finishFirst(new Response(JSON.stringify(first)));
    await waitFor(() => expect(screen.getByText(/Training identity: run-b/)).toBeTruthy());
    expect(screen.queryByText(/Training identity: run-a/)).toBeNull();
  });
});

it('shows an accounting admission block without treating unknown cost as free', async () => {
  const data = report();
  data.budget.admission_blocked = true;
  data.budget.blocking_reasons = ['A linked provider attempt has unsettled accounting.'];
  data.budget.conservative_commitment_usd = 12;
  respond(data);
  render(<TrainingPage />);
  expect(await screen.findByText('New paid admissions blocked by accounting review.')).toBeTruthy();
  expect(screen.getByText('A linked provider attempt has unsettled accounting.')).toBeTruthy();
  expect(screen.getByText('Conservative commitment: $12.00')).toBeTruthy();
});
