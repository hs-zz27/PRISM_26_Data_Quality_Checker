import '@testing-library/jest-dom';

// Vitest 2 preserves Node's existing storage globals. On Node 26, localStorage
// exists but is undefined without --localstorage-file. Use this test environment's
// actual JSDOM storage, not Node's file-backed storage or a hand-written mock.
const jsdomWindow = (globalThis as typeof globalThis & {
  jsdom: { window: Window };
}).jsdom.window;

for (const name of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    enumerable: true,
    value: jsdomWindow[name],
    writable: true,
  });
}
