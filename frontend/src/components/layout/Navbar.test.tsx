import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { Navbar } from './Navbar';

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

function renderWithStatus(personModel: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const path = new URL(input, 'http://localhost').pathname;
    if (path === '/api/v1/health') return json({ status: 'ok', api_version: 'v1' });
    return json({
      ollama: { configuration: 'valid', reachable: true, model: 'qwen3-vl:4b-instruct', model_available: true },
      person_model: personModel,
      // The older detector is off; it must not decide the light.
      detector: { integration_supported: true, configuration: 'disabled', checkpoint_file: 'not_configured', validation: 'unknown', architecture: 'fasterrcnn_mobilenet_v3_large_320_fpn' },
      execution_device: 'cpu',
    });
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><Navbar /></MemoryRouter></QueryClientProvider>);
}

afterEach(() => vi.unstubAllGlobals());

describe('Mask R-CNN readiness light', () => {
  it('is ready when the person model weights are installed', async () => {
    renderWithStatus({ model: 'maskrcnn_resnet50_fpn_v2_coco', checkpoint_file: 'present' });
    expect(await screen.findByText('Mask R-CNN ready')).toBeInTheDocument();
    expect(screen.queryByText(/Detector/)).not.toBeInTheDocument();
  });

  it('is unavailable when the weights file is missing', async () => {
    renderWithStatus({ model: 'maskrcnn_resnet50_fpn_v2_coco', checkpoint_file: 'missing' });
    expect(await screen.findByText('Ollama ready')).toBeInTheDocument(); // the status has loaded
    expect(screen.getByText('Mask R-CNN Unavailable')).toBeInTheDocument();
  });
});
