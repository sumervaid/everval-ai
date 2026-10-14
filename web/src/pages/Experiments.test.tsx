import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewExperiment } from './Experiments';
import type { Workspace } from '../types';

const workspace: Workspace = {
  overview: {
    collections: 1,
    scenarios: 1,
    experiments: 0,
    completed_experiments: 0,
    pass_rate: null,
    total_cost_usd: null,
    recent_experiments: [],
  },
  settings: {
    data_dir: '/workspace',
    version: '0.1.0',
    python_version: '3.12',
    storage_free_bytes: 1000,
    docker_available: false,
    remote_providers_enabled: false,
    worker_running: true,
    auth_enabled: false,
  },
  experiments: [],
  collections: [
    {
      id: 'collection-1',
      name: 'Invoice reconciliation',
      description: 'Invoice tests',
      domain: 'data',
      version: '1.0.0',
      created_at: '2026-10-13T10:00:00Z',
      scenarios: [
        {
          id: 'task-1',
          name: 'Match amount',
          description: '',
          domain: 'data',
          instruction: 'Match the total',
          inputs: { amount: 42 },
          expected: {},
          checks: [],
          reference_output: { amount: 42 },
          tags: [],
          provenance: {},
        },
      ],
    },
  ],
  systems: [
    {
      id: 'reference',
      name: 'Reference control',
      kind: 'reference',
      model: '',
      base_url: '',
      api_key_env: '',
      temperature: 0,
      max_tokens: 100,
      input_cost_per_million: 0,
      output_cost_per_million: 0,
    },
    {
      id: 'replay',
      name: 'Imported agent',
      kind: 'replay',
      model: '',
      base_url: '',
      api_key_env: '',
      temperature: 0,
      max_tokens: 100,
      input_cost_per_million: null,
      output_cost_per_million: null,
    },
  ],
};
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = '';
});
describe('experiment protocol', () => {
  it('requires a system and distinguishes fixture controls from imported submissions', () => {
    render(<NewExperiment workspace={workspace} refresh={async () => {}} />);
    expect(
      (screen.getByRole('button', { name: 'Start experiment' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.queryByText('Imported agent')).toBeNull();
    fireEvent.click(screen.getByRole('checkbox', { name: /Reference control/ }));
    expect(screen.getByText('Fixture-only experiment')).toBeTruthy();
    expect(
      screen.getByText('This validates the pipeline. It does not measure model performance.'),
    ).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Start experiment' }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });
  it('submits the selected cohort, repetition count, and budget without inventing results', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: 'run-1' }), { status: 200 }));
    vi.stubGlobal('fetch', fetcher);
    const refresh = vi.fn().mockResolvedValue(undefined);
    render(<NewExperiment workspace={workspace} refresh={refresh} />);
    fireEvent.click(screen.getByRole('checkbox', { name: /Reference control/ }));
    fireEvent.change(screen.getByLabelText('Repetitions per scenario'), { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('Total provider budget · USD'), {
      target: { value: '1.25' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start experiment' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(fetcher.mock.calls[0][0]).toBe('/api/experiments');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      name: 'Invoice reconciliation comparison',
      collection_id: 'collection-1',
      system_ids: ['reference'],
      repetitions: 3,
      budget_usd: 1.25,
    });
    await waitFor(() => expect(window.location.hash).toBe('#/experiments/run-1'));
    expect(refresh).toHaveBeenCalledTimes(1);
  });
  it('preserves the admission error when the service rejects a run', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: 'Provider budget is required' }), { status: 400 }),
        ),
    );
    render(<NewExperiment workspace={workspace} refresh={async () => {}} />);
    fireEvent.click(screen.getByRole('checkbox', { name: /Reference control/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Start experiment' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Provider budget is required'),
    );
    expect(window.location.hash).toBe('');
  });
});
