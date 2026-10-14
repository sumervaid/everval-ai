import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ProductionBatch, ProductionImports } from './ProductionCases';
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});
it('requires independent expected behavior and publishes only after validation approval', async () => {
  let approved = false;
  const batch = () => ({
    id: 'batch',
    filename: 'production.json',
    count: 1,
    privacy_note: 'Inspect redacted examples.',
    cases: [
      {
        id: 'case',
        name: 'Invoice incident',
        domain: 'data',
        instruction: 'Add amounts.',
        inputs: { amounts: [20, 22] },
        observed_output: { total: 999 },
        status: approved ? 'approved' : 'staged',
        approval: approved
          ? { expected_behavior: 'Return the exact sum.', rationale: 'Verified source amounts.' }
          : null,
      },
    ],
  });
  const fetcher = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/approve')) {
      approved = true;
      return Promise.resolve(new Response('{}'));
    }
    if (url.endsWith('/publish'))
      return Promise.resolve(new Response(JSON.stringify({ collection: { id: 'collection' } })));
    return Promise.resolve(new Response(JSON.stringify(batch())));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<ProductionBatch id="batch" refresh={async () => {}} />);
  await screen.findByRole('heading', { name: 'production.json' });
  expect(
    (screen.getByRole('button', { name: 'Publish 0 approved cases' }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: /Invoice incident/ }));
  fireEvent.change(screen.getByLabelText('What should the correct behavior be?'), {
    target: { value: 'Return the exact sum.' },
  });
  fireEvent.change(screen.getByLabelText('Why is this expectation correct?'), {
    target: { value: 'Verified source amounts.' },
  });
  fireEvent.change(screen.getByLabelText('Output field 1'), { target: { value: 'total' } });
  fireEvent.change(screen.getByLabelText('Comparison 1'), { target: { value: 'numeric' } });
  fireEvent.change(screen.getByLabelText('Approved expected value 1'), { target: { value: '42' } });
  fireEvent.click(screen.getByRole('button', { name: 'Validate and approve expected behavior' }));
  await screen.findByText(/This case has an immutable approval/);
  const call = fetcher.mock.calls.find(
    (args: unknown[]) => args[0] === '/api/production-cases/case/approve',
  );
  const body = JSON.parse(call?.[1].body as string);
  expect(body.reference_output).toEqual({ total: 42 });
  expect(body.checks[0].expected).toBe(42);
  expect(body).not.toHaveProperty('observed_output');
  fireEvent.change(screen.getByLabelText('Collection name'), {
    target: { value: 'Invoice regressions' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Publish 1 approved case' }));
  await waitFor(() =>
    expect(
      fetcher.mock.calls.some((args: unknown[]) => args[0] === '/api/case-imports/batch/publish'),
    ).toBe(true),
  );
});
it('sends field and exact-text redactions with the selected upload', async () => {
  const fetcher = vi
    .fn()
    .mockImplementation((_url: string, options?: RequestInit) =>
      Promise.resolve(
        new Response(JSON.stringify(options?.method === 'POST' ? { id: 'batch' } : [])),
      ),
    );
  vi.stubGlobal('fetch', fetcher);
  render(<ProductionImports />);
  fireEvent.change(screen.getByLabelText('Examples file'), {
    target: { files: [new File(['[]'], 'incidents.json', { type: 'application/json' })] },
  });
  fireEvent.change(screen.getByLabelText('Redact exact text'), {
    target: { value: 'Customer identifier' },
  });
  const submit = screen.getByRole('button', {
    name: 'Redact and stage examples',
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(false);
  fireEvent.submit(submit.closest('form')!);
  await waitFor(() => expect(fetcher.mock.calls.length).toBe(2));
  const form = fetcher.mock.calls[1][1].body as FormData;
  expect(form.get('redacted_literals')).toBe('["Customer identifier"]');
  expect(JSON.parse(form.get('redacted_fields') as string)).toContain('email');
});
