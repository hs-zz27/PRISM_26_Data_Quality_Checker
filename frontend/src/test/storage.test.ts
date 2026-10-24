import { afterEach, describe, expect, it } from 'vitest';

const key = 'prism.test.storage';

afterEach(() => {
  localStorage.removeItem(key);
  sessionStorage.removeItem(key);
});

describe('browser storage environment', () => {
  it('uses JSDOM storage rather than the Node storage global', () => {
    const dom = (globalThis as typeof globalThis & { jsdom: { window: Window } }).jsdom;
    expect(localStorage).toBe(dom.window.localStorage);
    expect(sessionStorage).toBe(dom.window.sessionStorage);
    expect(localStorage).toBeInstanceOf(Storage);
  });

  it('persists string values and removes them with browser semantics', () => {
    expect(localStorage.getItem(key)).toBeNull();
    localStorage.setItem(key, 'saved');
    expect(localStorage.getItem(key)).toBe('saved');
    localStorage.removeItem(key);
    expect(localStorage.getItem(key)).toBeNull();
  });

  it('keeps session storage separate from local storage', () => {
    localStorage.setItem(key, 'local');
    sessionStorage.setItem(key, 'session');
    expect(localStorage.getItem(key)).toBe('local');
    expect(sessionStorage.getItem(key)).toBe('session');
  });
});
