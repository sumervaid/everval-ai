import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ChatPage, type Conversation } from './Chat';

const catalog = {
  systems: [{ id: 'fixture-reference', name: 'Reference', model: '', kind: 'reference' }],
  collections: [{ id: 'tickets', name: 'Support tickets', scenario_count: 3 }],
};
const blank: Conversation = {
  id: 'chat-1',
  status: 'draft',
  ready: false,
  missing: ['models'],
  messages: [],
  draft: {},
};
const active: Conversation = {
  ...blank,
  status: 'active',
  ready: true,
  missing: [],
  messages: [
    { role: 'user', content: 'Compare Reference on Support tickets every 2 weeks in HTML.' },
    { role: 'assistant', content: 'Your benchmark is scheduled every 2 weeks.' },
  ],
  draft: {
    name: 'Support check',
    collection_id: 'tickets',
    system_ids: ['fixture-reference'],
    interval_minutes: 20160,
    report_formats: ['html'],
    budget_usd: 0,
    lifetime_budget_usd: 0,
  },
  experiment_id: 'run-1',
};
function mockServer(options: { failMessage?: boolean; history?: Conversation[] } = {}) {
  let session = blank;
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/conversations/catalog') return Response.json(catalog);
    if (url === '/api/conversations' && init?.method === 'POST') return Response.json(blank);
    if (url === '/api/conversations') return Response.json(options.history ?? []);
    if (url.endsWith('/messages')) {
      if (options.failMessage)
        return Response.json({ detail: 'No model matches that name.' }, { status: 400 });
      session = active;
      return Response.json(active);
    }
    if (url.endsWith('/reports')) return Response.json([]);
    if (url === '/api/conversations/chat-1') return Response.json(options.history?.[0] ?? session);
    throw new Error('Unexpected request: ' + url);
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('conversational benchmark setup', () => {
  it('keeps opening and example selection free of mutations', async () => {
    const fetcher = mockServer();
    render(<ChatPage />);
    const example = await screen.findByRole('button', { name: /TRY A REQUEST/ });
    await waitFor(() => expect(example.hasAttribute('disabled')).toBe(false));
    fireEvent.click(example);
    expect(
      (screen.getByRole('textbox', { name: 'Your benchmark request' }) as HTMLTextAreaElement)
        .value,
    ).toContain('every 2 weeks');
    expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
  });

  it('submits the actual request and shows the scheduled plan and results link', async () => {
    const fetcher = mockServer();
    render(<ChatPage />);
    const textbox = screen.getByRole('textbox', { name: 'Your benchmark request' });
    await waitFor(() => expect(textbox.hasAttribute('disabled')).toBe(false));
    const text = 'Compare Reference on Support tickets every 2 weeks in HTML.';
    fireEvent.change(textbox, { target: { value: text } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('Your benchmark is scheduled every 2 weeks.');
    expect(screen.getByText('Every 2 weeks')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'View results' }).getAttribute('href')).toBe(
      '#/experiments/run-1',
    );
    const messages = fetcher.mock.calls.filter(([url]) => url.endsWith('/messages'));
    expect(messages).toHaveLength(1);
    expect(JSON.parse(messages[0][1]!.body as string)).toEqual({ message: text });
    expect((textbox as HTMLTextAreaElement).value).toBe('');
  });

  it('retains the user request on rejection so it can be corrected', async () => {
    mockServer({ failMessage: true });
    render(<ChatPage />);
    const textbox = screen.getByRole('textbox', { name: 'Your benchmark request' });
    await waitFor(() => expect(textbox.hasAttribute('disabled')).toBe(false));
    fireEvent.change(textbox, { target: { value: 'Compare missing-model every 2 weeks.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByRole('alert');
    expect(screen.getByText('No model matches that name.')).toBeTruthy();
    expect((textbox as HTMLTextAreaElement).value).toBe('Compare missing-model every 2 weeks.');
  });

  it('resumes a saved conversation without creating another run', async () => {
    const fetcher = mockServer({ history: [active] });
    render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: /Support check/ }));
    await screen.findByText('Your benchmark is scheduled every 2 weeks.');
    expect(fetcher.mock.calls.every(([, init]) => !init?.method)).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Pause future runs' }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([url, init]) =>
            url.endsWith('/messages') && JSON.parse(init!.body as string).message === 'pause',
        ),
      ).toBe(true),
    );
  });
  it('does not let a poll overwrite a newly activated conversation', async () => {
    let release!: (response: Response) => void;
    const messageResponse = new Promise<Response>((resolve) => {
      release = resolve;
    });
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/conversations/catalog') return Response.json(catalog);
      if (url === '/api/conversations' && init?.method === 'POST') return Response.json(blank);
      if (url === '/api/conversations') return Response.json([]);
      if (url.endsWith('/messages')) return messageResponse;
      if (url.endsWith('/reports')) return Response.json([]);
      if (url === '/api/conversations/chat-1') return Response.json(blank);
      throw new Error('Unexpected request: ' + url);
    });
    vi.stubGlobal('fetch', fetcher);
    render(<ChatPage />);
    const textbox = screen.getByRole('textbox', { name: 'Your benchmark request' });
    await waitFor(() => expect(textbox.hasAttribute('disabled')).toBe(false));
    fireEvent.change(textbox, {
      target: { value: 'Compare Reference on Support tickets every two weeks.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() =>
      expect(fetcher.mock.calls.some(([url]) => url.endsWith('/messages'))).toBe(true),
    );
    expect(fetcher.mock.calls.filter(([url]) => url === '/api/conversations/chat-1')).toHaveLength(
      0,
    );
    release(Response.json(active));
    await screen.findByText('Your benchmark is scheduled every 2 weeks.');
    await waitFor(() => expect(screen.getByRole('link', { name: 'View results' })).toBeTruthy());
  });

  it('surfaces retained scheduling and report errors', async () => {
    const failed = {
      ...active,
      status: 'paused',
      schedule: { last_error: 'The total spending cap is reached.' },
      report_errors: [{ experiment_id: 'run-1', error: 'The report folder is unavailable.' }],
    };
    mockServer({ history: [failed] });
    render(<ChatPage />);
    fireEvent.click(await screen.findByRole('button', { name: /Support check/ }));
    await screen.findByText(/The total spending cap is reached/);
    await screen.findByText(/The report folder is unavailable/);
    expect(screen.getByRole('button', { name: 'Resume' })).toBeTruthy();
  });
});
