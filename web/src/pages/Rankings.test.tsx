import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import type {
  RankingBenchmark,
  RankingCell,
  RankingsReport,
  RankingSystem,
} from '../rankings-types';
import { RankingsPage } from './Rankings';

const benchmark = (id: string): RankingBenchmark => ({
  id,
  name: 'Invoice checks',
  version: '1.0',
  domain: 'data',
  collection_id: 'invoices',
  collection_hash: 'collection-' + id,
  contract_hash: 'contract-' + id,
  repetitions: 2,
  scenario_count: 5,
  experiment_count: 1,
  latest_created_at: '2026-10-16T12:00:00Z',
  benchmark_release_id: null,
  benchmark_partition: 'development',
});
const system = (id: string, name: string, extra: Partial<RankingSystem> = {}): RankingSystem => ({
  id,
  system_ids: [id],
  name,
  model: 'test-model',
  kind: 'http',
  harness: 'single-turn',
  fixture_control: false,
  submission_replay: false,
  external_adapter: false,
  ...extra,
});
const cell = (systemId: string, extra: Partial<RankingCell> = {}): RankingCell => ({
  benchmark_id: 'cohort-a',
  system_id: systemId,
  source_system_id: systemId,
  experiment_id: 'run/' + systemId,
  experiment_name: 'Invoice comparison',
  created_at: '2026-10-16T12:00:00Z',
  status: 'completed',
  pass_rate: 0.8,
  passed: 8,
  scheduled: 10,
  completed: 10,
  completion_rate: 1,
  ci_low: 0.5,
  ci_high: 0.95,
  interval_method: 'test interval',
  known_cost_usd: 1,
  cost_usd: 1,
  cost_complete: true,
  cost_per_success_usd: 0.125,
  mean_duration_ms: 900,
  median_duration_ms: 1000,
  p95_duration_ms: 1200,
  duration_complete: true,
  duration_recorded: 10,
  ranks: { quality: 1, cost: 2, latency: 1 },
  eligibility: { quality: true, cost: true, latency: true },
  issues: [],
  ...extra,
});
function report(): RankingsReport {
  return {
    schema_version: 1,
    warnings: ['Ranks describe observations within the same frozen cohort.'],
    benchmarks: [benchmark('cohort-a'), benchmark('cohort-b')],
    systems: [
      system('config-a', 'Careful configuration'),
      system('config-b', 'Fast configuration'),
      system('control', 'Reference control', {
        model: '',
        kind: 'reference',
        fixture_control: true,
      }),
      system('replay', 'Stored submission', { model: '', kind: 'replay', submission_replay: true }),
    ],
    cells: [
      cell('config-a'),
      cell('config-b', {
        cost_usd: 0.8,
        known_cost_usd: 0.8,
        cost_per_success_usd: 0.1,
        p95_duration_ms: 2000,
        ranks: { quality: 1, cost: 1, latency: 2 },
      }),
      cell('control', {
        pass_rate: 1,
        passed: 10,
        cost_usd: 0,
        known_cost_usd: 0,
        cost_per_success_usd: 0,
        ranks: { quality: null, cost: null, latency: null },
        eligibility: { quality: false, cost: false, latency: false },
      }),
      cell('replay', {
        pass_rate: 0.5,
        passed: 5,
        ranks: { quality: null, cost: null, latency: null },
        eligibility: { quality: false, cost: false, latency: false },
      }),
      cell('config-a', {
        benchmark_id: 'cohort-b',
        pass_rate: 0.5,
        passed: 5,
        cost_usd: null,
        cost_complete: false,
        cost_per_success_usd: 0.2,
        duration_complete: false,
        duration_recorded: 9,
        p95_duration_ms: 900,
        ranks: { quality: 1, cost: null, latency: null },
        eligibility: { quality: true, cost: false, latency: false },
        issues: ['Cost is incomplete; known spending is a lower bound.'],
      }),
    ],
    window: {
      limit: 100,
      listed_runs: 120,
      scanned_runs: 100,
      truncated: true,
      store_list_limit: 500,
    },
    counts: {
      benchmarks: 2,
      systems: 4,
      cells: 5,
      ranked_cells: 3,
      experiments_scanned: 100,
      experiments_skipped: 0,
    },
  };
}
function respond(data = report()) {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(data)));
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = '';
});

describe('benchmark ranking matrix', () => {
  it('shows exact benchmark/configuration identities, ties, and absent cells without inventing a zero', async () => {
    const fetcher = respond();
    render(<RankingsPage />);
    const matrix = await screen.findByRole('table');
    expect(fetcher.mock.calls[0][0]).toBe('/api/rankings?limit=100');
    expect(within(matrix).getAllByRole('columnheader')).toHaveLength(3);
    expect(within(matrix).getAllByRole('rowheader')).toHaveLength(4);
    expect(within(matrix).getAllByText('test-model')).toHaveLength(2);
    expect(within(matrix).getByText('cohort-a')).toBeTruthy();
    expect(within(matrix).getByText('cohort-b')).toBeTruthy();
    expect(within(matrix).getByText('config-a')).toBeTruthy();
    expect(within(matrix).getByText('config-b')).toBeTruthy();
    expect(within(matrix).getAllByText('Tied #1')).toHaveLength(2);
    expect(within(matrix).getAllByText('No result in this window')).toHaveLength(3);
    expect(screen.getByText(/Older runs are outside this window/)).toBeTruthy();
    expect(screen.queryByText('Overall rank')).toBeNull();
  });

  it('switches independent metrics and withholds incomplete costs and timing ranks', async () => {
    respond();
    render(<RankingsPage />);
    await screen.findByRole('table');
    fireEvent.click(screen.getByRole('button', { name: 'Cost per success' }));
    expect(
      screen.getByRole('button', {
        name: 'Careful configuration, Invoice checks, Cost per success: $0.13, #2',
      }),
    ).toBeTruthy();
    expect(
      screen.getByRole('button', {
        name: 'Fast configuration, Invoice checks, Cost per success: $0.10, #1',
      }),
    ).toBeTruthy();
    const partial = screen.getByRole('button', {
      name: 'Careful configuration, Invoice checks, Cost per success: —, Unranked',
    });
    expect(within(partial).getByText('Cost incomplete')).toBeTruthy();
    expect(within(partial).queryByText('$0.20')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'P95 latency' }));
    expect(
      screen.getByRole('button', {
        name: 'Careful configuration, Invoice checks, P95 latency: 1.2 s, #1',
      }),
    ).toBeTruthy();
    const timing = screen.getByRole('button', {
      name: 'Careful configuration, Invoice checks, P95 latency: 900 ms, Unranked',
    });
    expect(within(timing).getByText('Timing incomplete')).toBeTruthy();
  });

  it('keeps fixture controls and recorded submissions visible but unranked for every metric', async () => {
    respond();
    render(<RankingsPage />);
    await screen.findByRole('table');
    for (const metric of ['Quality', 'Cost per success', 'P95 latency']) {
      fireEvent.click(screen.getByRole('button', { name: metric }));
      const control = screen.getByRole('button', {
        name: new RegExp('Reference control, Invoice checks, ' + metric + '.*Unranked'),
      });
      const replay = screen.getByRole('button', {
        name: new RegExp('Stored submission, Invoice checks, ' + metric + '.*Unranked'),
      });
      expect(within(control).getByText('Fixture control')).toBeTruthy();
      expect(within(replay).getByText('Imported evidence')).toBeTruthy();
      expect(control.className).toContain('rankings-unranked');
      expect(replay.className).toContain('rankings-unranked');
    }
  });

  it('opens evidence for a selected cell and links to the encoded source experiment', async () => {
    respond();
    render(<RankingsPage />);
    const selected = await screen.findByRole('button', {
      name: 'Careful configuration, Invoice checks, Quality: 50.0%, #1',
    });
    fireEvent.click(selected);
    expect(selected.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('link', { name: 'Open experiment' }).getAttribute('href')).toBe(
      '#/experiments/run%2Fconfig-a',
    );
    expect(screen.getByText('$1.00 known spend · lower bound')).toBeTruthy();
    expect(screen.getByText('Cost incomplete · not ranked')).toBeTruthy();
    expect(screen.getByText('9 / 10 timings recorded')).toBeTruthy();
    expect(screen.getByText('95% interval 50.0%–95.0%')).toBeTruthy();
    expect(screen.getByText('Cost is incomplete; known spending is a lower bound.')).toBeTruthy();
  });

  it('filters models and exact cohorts while preserving ranks computed over all peers', async () => {
    respond();
    render(<RankingsPage />);
    await screen.findByRole('table');
    fireEvent.change(screen.getByLabelText('Find a model or configuration'), {
      target: { value: 'Fast' },
    });
    const matrix = screen.getByRole('table');
    expect(within(matrix).getAllByRole('rowheader')).toHaveLength(1);
    expect(within(matrix).getByText('Tied #1')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Benchmark'), { target: { value: 'cohort-b' } });
    expect(within(matrix).getAllByRole('columnheader')).toHaveLength(2);
    expect(within(matrix).getByText('No result in this window')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Find a model or configuration'), {
      target: { value: 'Absent' },
    });
    expect(screen.getByText('No matching results')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('guides an empty workspace to an experiment without fixture leaderboard numbers', async () => {
    const empty = report();
    empty.benchmarks = [];
    empty.systems = [];
    empty.cells = [];
    respond(empty);
    render(<RankingsPage />);
    expect(await screen.findByText('Your rankings start with an experiment')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Create an experiment' }).getAttribute('href')).toBe(
      '#/experiments/new',
    );
    expect(screen.queryByRole('table')).toBeNull();
  });

  it('retains an API failure and supports an explicit refresh without starting a run', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Workspace is unavailable' }), { status: 503 }),
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(report())));
    vi.stubGlobal('fetch', fetcher);
    render(<RankingsPage />);
    expect((await screen.findByRole('alert')).textContent).toContain('Workspace is unavailable');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh results' }));
    expect(await screen.findByRole('table')).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(
      fetcher.mock.calls.every((call) => call[0] === '/api/rankings?limit=100' && !call[1].method),
    ).toBe(true);
  });

  it('explains withheld ranks when recent frozen evidence cannot be verified', async () => {
    const data = report();
    data.counts.experiments_skipped = 1;
    data.counts.ranked_cells = 0;
    for (const result of data.cells) {
      result.ranks = { quality: null, cost: null, latency: null };
      result.eligibility = { quality: false, cost: false, latency: false };
    }
    respond(data);
    render(<RankingsPage />);
    await screen.findByRole('table');
    expect(
      screen.getByText(/Rankings are withheld until that evidence can be checked/),
    ).toBeTruthy();
    expect(screen.queryByText('Tied #1')).toBeNull();
    expect(screen.getByText('How these rankings are calculated')).toBeTruthy();
    expect(
      screen.getByText(
        'Costs use the prices saved with each experiment; they are not provider invoice totals.',
      ),
    ).toBeTruthy();
  });

  it('is reachable from the operator navigation with its own title', async () => {
    window.location.hash = '#/rankings';
    vi.stubGlobal('scrollTo', vi.fn());
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        const data: Record<string, unknown> = {
          '/api/session': {
            id: 'operator',
            name: 'Local operator',
            mode: 'local',
            role: 'operator',
            capabilities: ['workspace.read', 'workspace.write'],
          },
          '/api/overview': {},
          '/api/collections': [],
          '/api/systems': [],
          '/api/experiments': [],
          '/api/settings': { version: '0.3.1' },
          '/api/rankings?limit=100': report(),
        };
        return Promise.resolve(new Response(JSON.stringify(data[url])));
      }),
    );
    render(<App />);
    await screen.findByRole('heading', { name: 'Model rankings' });
    const link = screen.getByRole('link', { name: 'Rankings' });
    expect(link.getAttribute('aria-current')).toBe('page');
    await waitFor(() => expect(document.title).toBe('Rankings · Everval AI'));
  });
});
