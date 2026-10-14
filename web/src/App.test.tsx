import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
  window.location.hash = '';
});
it('opens assigned reviews without fetching any operator-only workspace evidence', async () => {
  vi.stubGlobal('scrollTo', vi.fn());
  window.location.hash = '/';
  const session = {
    id: 'reviewer',
    name: 'Assigned reviewer',
    role: 'reviewer',
    mode: 'team',
    team_enabled: true,
    capabilities: ['identity.self', 'reviews.assigned'],
  };
  const fetcher = vi
    .fn()
    .mockImplementation((url: string) =>
      Promise.resolve(new Response(JSON.stringify(url === '/api/session' ? session : []))),
    );
  vi.stubGlobal('fetch', fetcher);
  render(<App />);
  await screen.findByRole('heading', { name: 'Your review desk' });
  await waitFor(() => expect(screen.getByText('No reviews assigned')).toBeTruthy());
  expect(
    fetcher.mock.calls.every((args: unknown[]) =>
      ['/api/session', '/api/review-inbox'].includes(args[0] as string),
    ),
  ).toBe(true);
  expect(screen.queryByRole('link', { name: 'Collections' })).toBeNull();
  expect(screen.queryByRole('link', { name: 'Team access' })).toBeNull();
});
