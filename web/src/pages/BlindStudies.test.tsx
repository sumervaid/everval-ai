import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { BlindStudiesPage, BlindStudyDetail, NewBlindStudy, type BlindStudy } from './BlindStudies';

const study: BlindStudy = {
  id: 'study-1',
  queue_id: 'queue-1',
  name: 'Blind study study-1',
  status: 'collecting',
  mode: 'trusted_custodian',
  created_at: '2026-10-26',
  locked_at: null,
  revealed_at: null,
  evidence_hash: 'a'.repeat(64),
  items: 1,
  assigned_reviews: 2,
  completed_reviews: 0,
  missing_reviews: 2,
  boundary: 'Trusted custodian',
};
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  location.hash = '';
});
function server(initial: BlindStudy, failLock = false) {
  let current = initial;
  const fetcher = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.endsWith('/lock')) {
      if (failLock)
        return Response.json({ detail: 'Evidence changed; refresh the study.' }, { status: 400 });
      current = { ...current, status: 'locked', evidence_hash: 'b'.repeat(64) };
    } else if (url.endsWith('/reveal')) {
      current = { ...current, status: 'revealed', name: 'Support comparison' };
    } else if (url.endsWith('/report')) {
      return Response.json({
        study: current,
        review: {
          preferences: { Alpha: 2, Beta: 0 },
          fixture_controls_present: true,
          interpretation: 'Recorded judgments.',
        },
        reveal: { reveal_rationale: 'Complete independent review.' },
      });
    }
    return Response.json(current);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
describe('dedicated blind studies', () => {
  it('shows viewer-safe progress without fetching operator catalog or reports', async () => {
    const fetcher = vi.fn(async () => Response.json([study]));
    vi.stubGlobal('fetch', fetcher);
    render(<BlindStudiesPage canManage={false} />);
    await screen.findByText('Blind study study-1');
    expect(screen.queryByRole('link', { name: 'New study' })).toBeNull();
    expect(fetcher.mock.calls).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledWith('/api/blind-studies', expect.anything());
  });
  it('withholds result controls while coverage is incomplete', async () => {
    const fetcher = server(study);
    render(<BlindStudyDetail id="study-1" canManage />);
    const lock = await screen.findByRole('button', { name: 'Lock completed judgments' });
    expect(lock.hasAttribute('disabled')).toBe(true);
    expect(screen.queryByText('HTML report')).toBeNull();
    expect(fetcher.mock.calls.every(([url]) => !url.endsWith('/report'))).toBe(true);
  });
  it('locks the viewed evidence and requires a reason before reveal', async () => {
    const fetcher = server({ ...study, completed_reviews: 2, missing_reviews: 0 });
    render(<BlindStudyDetail id="study-1" canManage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Lock completed judgments' }));
    const reveal = await screen.findByRole('button', { name: 'Reveal identities and results' });
    expect(reveal.hasAttribute('disabled')).toBe(true);
    expect(fetcher.mock.calls.find(([url]) => url.endsWith('/lock'))?.[1]?.body).toBe(
      JSON.stringify({ expected_hash: 'a'.repeat(64) }),
    );
    fireEvent.change(screen.getByRole('textbox', { name: 'Reason for revealing results' }), {
      target: { value: 'Complete independent review.' },
    });
    fireEvent.click(reveal);
    await screen.findByText('Alpha');
    expect(screen.getByText('Beta')).toBeTruthy();
    const mutation = fetcher.mock.calls.find(([url]) => url.endsWith('/reveal'));
    expect(JSON.parse(mutation![1]!.body as string)).toEqual({
      expected_hash: 'b'.repeat(64),
      rationale: 'Complete independent review.',
    });
    expect(screen.getByRole('button', { name: 'HTML report' })).toBeTruthy();
  });
  it('keeps a rejected stale lock sealed and surfaces the error', async () => {
    const fetcher = server({ ...study, completed_reviews: 2, missing_reviews: 0 }, true);
    render(<BlindStudyDetail id="study-1" canManage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Lock completed judgments' }));
    await screen.findByText('Evidence changed; refresh the study.');
    expect(screen.queryByRole('button', { name: 'Reveal identities and results' })).toBeNull();
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/report'))).toBe(false);
  });
  it('lets a viewer read the locked state without transition controls', async () => {
    server({ ...study, status: 'locked', completed_reviews: 2, missing_reviews: 0 });
    render(<BlindStudyDetail id="study-1" canManage={false} />);
    await screen.findByText('The study coordinator controls when the results are released.');
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.queryByText('HTML report')).toBeNull();
  });
  it('creates a frozen study only after selecting a pair, rubric and two reviewers', async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/catalog'))
        return Response.json({
          team_enabled: true,
          experiments: [{ id: 'run', name: 'Completed support run', status: 'completed' }],
          rubrics: [{ id: 'rubric', name: 'Helpfulness', version: '1' }],
          reviewers: [
            { id: 'one', name: 'Reviewer One' },
            { id: 'two', name: 'Reviewer Two' },
          ],
        });
      if (url === '/api/experiments/run')
        return Response.json({
          snapshot: {
            systems: [
              { id: 'alpha', name: 'Alpha' },
              { id: 'beta', name: 'Beta' },
              { id: 'gamma', name: 'Gamma' },
            ],
          },
        });
      if (init?.method === 'POST') return Response.json(study);
      throw new Error('Unexpected request ' + url);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<NewBlindStudy />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Study name' }), {
      target: { value: 'Support review' },
    });
    await screen.findByRole('option', { name: 'Completed support run' });
    const submit = screen.getByRole('button', { name: 'Freeze and assign study' });
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByRole('combobox', { name: 'Completed experiment' }), {
      target: { value: 'run' },
    });
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Alpha' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Beta' }));
    expect(screen.getByRole('checkbox', { name: 'Gamma' }).hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByRole('combobox', { name: 'Scoring rubric' }), {
      target: { value: 'rubric' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Reviewer One' }));
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Reviewer Two' }));
    fireEvent.click(submit);
    await waitFor(() => expect(location.hash).toBe('#/blind/study-1'));
    const mutations = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(mutations).toHaveLength(1);
    expect(JSON.parse(mutations[0][1]!.body as string)).toEqual({
      name: 'Support review',
      experiment_id: 'run',
      system_ids: ['alpha', 'beta'],
      reviewer_ids: ['one', 'two'],
      rubric_id: 'rubric',
      mode: 'trusted_custodian',
    });
  });
});
