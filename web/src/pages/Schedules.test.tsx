import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScheduleForm } from './Schedules';
import type { Experiment } from '../types';
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe('recurring evaluation admission', () => {
  it('discloses the per-run budget and creates a schedule only on explicit submission', async () => {
    const experiment = { id: 'run-1', name: 'Policy check', budget_usd: 2.5 } as Experiment;
    const fetcher = vi.fn().mockResolvedValue(new Response('{}'));
    vi.stubGlobal('fetch', fetcher);
    render(<ScheduleForm experiment={experiment} onClose={() => {}} />);
    expect(screen.getByText('$2.50')).toBeTruthy();
    expect(screen.getByText(/local service must remain running/)).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('Run every · minutes'), { target: { value: '120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create recurring schedule' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(fetcher.mock.calls[0][0]).toBe('/api/schedules');
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      name: 'Policy check · recurring',
      experiment_id: 'run-1',
      interval_minutes: 120,
      enabled: true,
    });
    await waitFor(() => expect(screen.getByText(/Schedule created/)).toBeTruthy());
  });
});
