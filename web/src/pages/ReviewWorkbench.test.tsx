import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ReviewTask, QueueBrief } from './ReviewWorkbench';
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
const rubric = {
  id: 'rubric',
  name: 'Correctness',
  version: '1',
  hash: 'r',
  instructions: 'Assess the task.',
  dimensions: [{ id: 'correct', name: 'Correct', guidance: 'One means all requirements are met.' }],
};
it('scores both anonymous responses and sends judgments without a caller-chosen identity', async () => {
  const item = {
    id: 'item',
    queue_name: 'Blind review',
    blind: true,
    rubric,
    task: {
      name: 'Invoice sum',
      domain: 'data',
      instruction: 'Add 20 and 22.',
      inputs: { amounts: [20, 22] },
    },
    responses: { A: { output: { total: 42 } }, B: { output: { total: 999 } } },
    previous: null,
    blinding_note: 'System metadata is withheld.',
  };
  const fetcher = vi
    .fn()
    .mockImplementation((_url: string, options?: RequestInit) =>
      Promise.resolve(
        new Response(JSON.stringify(options?.method === 'POST' ? { id: 'saved' } : item)),
      ),
    );
  vi.stubGlobal('fetch', fetcher);
  render(<ReviewTask id="item" />);
  await screen.findByRole('heading', { name: 'Invoice sum' });
  fireEvent.change(screen.getByLabelText('Response A · Correct'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Response B · Correct'), { target: { value: '0' } });
  fireEvent.change(screen.getByLabelText('Which response better satisfies the task?'), {
    target: { value: 'A' },
  });
  fireEvent.change(screen.getByLabelText('Overall judgment'), { target: { value: 'acceptable' } });
  fireEvent.change(screen.getByLabelText('Explain your judgment'), {
    target: { value: 'A computes the sum correctly.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Record judgment' }));
  await screen.findByText(/Review recorded/);
  const call = fetcher.mock.calls.find(
    (args: unknown[]) => args[0] === '/api/review-items/item/judgments',
  );
  expect(JSON.parse(call?.[1].body as string)).toEqual({
    scores: { A: { correct: 1 }, B: { correct: 0 } },
    preference: 'A',
    disposition: 'acceptable',
    rationale: 'A computes the sum correctly.',
  });
  expect(screen.queryByLabelText(/reviewer/i)).toBeNull();
});
it('binds a recorded decision to the exact displayed brief fingerprint', async () => {
  const brief = {
    queue_id: 'queue',
    name: 'Reviewed invoices',
    evidence_hash: 'a'.repeat(64),
    rubric,
    assigned_reviews: 2,
    completed_reviews: 1,
    preferences: { 'Reviewed system': 1 },
    dispositions: { acceptable: 1 },
    interpretation: 'Human judgments.',
    decisions: [],
  };
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      Promise.resolve(new Response(JSON.stringify(url.endsWith('/reviewers') ? [] : brief))),
    );
  vi.stubGlobal('fetch', fetcher);
  render(<QueueBrief id="queue" canManage />);
  await screen.findByRole('heading', { name: 'Reviewed invoices' });
  expect(screen.getByText(/Review coverage is incomplete/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText('Decision rationale'), {
    target: { value: 'More reviewers are needed.' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Record decision with evidence' }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some(
        (args: unknown[]) => args[0] === '/api/review-queues/queue/decisions',
      ),
    ).toBe(true),
  );
  const call = fetcher.mock.calls.find(
    (args: unknown[]) => args[0] === '/api/review-queues/queue/decisions',
  );
  expect(JSON.parse(call?.[1].body as string).evidence_hash).toBe(brief.evidence_hash);
});

it('makes locked study reviews read-only', async () => {
  const item = {
    id: 'locked-item',
    queue_name: 'Blind study',
    blind: true,
    rubric,
    locked: true,
    study_id: 'study',
    study_status: 'locked',
    task: { name: 'Locked invoice', domain: 'data', instruction: 'Add amounts.', inputs: {} },
    responses: { A: { output: { total: 5 } }, B: { output: { total: 0 } } },
    previous: null,
    blinding_note: 'Identities withheld.',
  };
  const fetcher = vi.fn(async () => Response.json(item));
  vi.stubGlobal('fetch', fetcher);
  render(<ReviewTask id="locked-item" />);
  await screen.findByText(
    'These judgments are locked. You can read your review, but it cannot be changed.',
  );
  expect(screen.getByRole('button', { name: 'Record judgment' }).hasAttribute('disabled')).toBe(
    true,
  );
  expect(screen.getAllByRole('combobox').every((input) => input.hasAttribute('disabled'))).toBe(
    true,
  );
  expect(
    screen.getByRole('textbox', { name: 'Explain your judgment' }).hasAttribute('disabled'),
  ).toBe(true);
  expect(fetcher.mock.calls).toHaveLength(1);
});
it('routes a sealed legacy queue to its study without decision or assignment forms', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      Response.json(
        url.endsWith('/reviewers')
          ? []
          : {
              study_id: 'study',
              study_status: 'collecting',
              name: 'Blind study',
              rubric: { name: 'Sealed' },
              preferences: {},
              assigned_reviews: 2,
              completed_reviews: 0,
            },
      ),
    ),
  );
  render(<QueueBrief id="queue" canManage />);
  expect((await screen.findByRole('link', { name: 'Open blind study' })).getAttribute('href')).toBe(
    '#/blind/study',
  );
  expect(screen.queryByRole('button', { name: 'Record decision with evidence' })).toBeNull();
  expect(screen.queryByRole('checkbox')).toBeNull();
});
