import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewCollection } from './Collections';
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.location.hash = '';
});
function enterTask() {
  fireEvent.change(screen.getByRole('textbox', { name: 'Collection name' }), {
    target: { value: 'Invoice review' },
  });
  fireEvent.change(screen.getByLabelText('Scenario name'), { target: { value: 'Find total' } });
  fireEvent.change(screen.getByLabelText('Task instruction'), {
    target: { value: 'Return the invoice total as a JSON object.' },
  });
}
describe('scenario authoring', () => {
  it('keeps public inputs and evaluator-only answers in separate manifest fields', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ id: 'collection-new' })));
    vi.stubGlobal('fetch', fetcher);
    render(<NewCollection refresh={async () => {}} />);
    enterTask();
    fireEvent.change(screen.getByLabelText('Public inputs · JSON object'), {
      target: { value: '{"invoice": "3 units at $10"}' },
    });
    fireEvent.change(screen.getByLabelText('Reference output · JSON object'), {
      target: { value: '{"total": 30}' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create collection' }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const collection = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(collection.scenarios[0].inputs).toEqual({ invoice: '3 units at $10' });
    expect(collection.scenarios[0].reference_output).toEqual({ total: 30 });
    expect(collection.scenarios[0].checks[0].expected).toEqual({ total: 30 });
    expect(collection.scenarios[0].instruction).not.toContain('30');
    await waitFor(() => expect(window.location.hash).toBe('#/collections/collection-new'));
  });
  it('rejects a non-object source before sending a collection', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    render(<NewCollection refresh={async () => {}} />);
    enterTask();
    fireEvent.change(screen.getByLabelText('Public inputs · JSON object'), {
      target: { value: '[1,2,3]' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create collection' }));
    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Inputs must be a JSON object.'),
    );
    expect(fetcher).not.toHaveBeenCalled();
  });
});
