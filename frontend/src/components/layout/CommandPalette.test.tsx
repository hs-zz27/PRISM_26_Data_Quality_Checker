import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { CommandPalette } from './CommandPalette';

function CurrentRoute() {
  const location = useLocation();
  return <output data-testid="current-route">{location.pathname}</output>;
}

describe('CommandPalette', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('opens with Ctrl+K and navigates to a selected screen', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      items: [], limit: 100, offset: 0, total: 0, next_offset: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/']}><CurrentRoute /><CommandPalette /></MemoryRouter></QueryClientProvider>);

    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(await screen.findByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('option', { name: /Audits/ }));

    await waitFor(() => expect(screen.getByTestId('current-route')).toHaveTextContent('/audits'));
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).not.toBeInTheDocument();
  });
});
