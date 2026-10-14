import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TrialReviews } from './Reviews';
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
describe('human judgment records', () => {
  it('records an explicit reviewer, dimension, and rationale independently of automated results', async () => {
    const saved = {
      id: 'review-1',
      trial_id: 'trial-1',
      reviewer: 'Operations team',
      dimension: 'completeness',
      score: 0.75,
      rationale: 'The response omitted the renewal exception.',
      created_at: '2026-10-13T10:00:00Z',
      latest: true,
    };
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('[]'))
      .mockResolvedValueOnce(new Response(JSON.stringify(saved)))
      .mockResolvedValueOnce(new Response(JSON.stringify([saved])));
    vi.stubGlobal('fetch', fetcher);
    render(<TrialReviews experimentId="experiment-1" trialId="trial-1" />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/Reviewer names are supplied labels/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add review' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Reviewer label' }), {
      target: { value: 'Operations team' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Dimension' }), {
      target: { value: 'completeness' },
    });
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Score · 0 to 1' }), {
      target: { value: '.75' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Rationale' }), {
      target: { value: 'The response omitted the renewal exception.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Record review' }));
    await waitFor(() => expect(screen.getByText('Operations team')).toBeTruthy());
    expect(fetcher.mock.calls[1][0]).toBe('/api/experiments/experiment-1/reviews');
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({
      trial_id: 'trial-1',
      reviewer: 'Operations team',
      dimension: 'completeness',
      score: 0.75,
      rationale: 'The response omitted the renewal exception.',
    });
    expect(screen.getByText('75.0%')).toBeTruthy();
  });
});
