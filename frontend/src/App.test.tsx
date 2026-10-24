import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { App } from './App';

describe('App Smoke Test', () => {
  it('renders without throwing error', () => {
    const { container } = render(<App />);
    expect(container).toBeTruthy();
  });
});
