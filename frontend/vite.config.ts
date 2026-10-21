import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { readFileSync } from 'node:fs';

const examplesPath = path.resolve(__dirname, '../backend/api/examples.json');
const examples = JSON.parse(readFileSync(examplesPath, 'utf8')) as {
  operations: Array<{
    operation_id: string;
    method: string;
    path: string;
    implementation: 'implemented' | 'planned';
    request: unknown;
    response: unknown;
  }>;
};
const operationIndex = examples.operations.map(({ operation_id, method, path: route, implementation }) => ({
  operation_id,
  method,
  path: route,
  implementation,
}));
const mockFixtures = examples.operations
  .filter((operation) => operation.implementation === 'planned')
  .map(({ operation_id, method, path: route, request, response }) => ({ operation_id, method, path: route, request, response }));

// https://vitejs.dev/config/
export default defineConfig(({ command }) => ({
  define: {
    __PRISM_OPERATION_INDEX__: JSON.stringify(operationIndex),
    __PRISM_MOCK_FIXTURES__: JSON.stringify(command === 'serve' ? mockFixtures : []),
  },
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // All /api/v1 requests proxy to the FastAPI backend.
      // CORS is not enabled on the backend; this proxy avoids it entirely.
      '/api/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        // No rewrite needed: backend routes already include /api/v1
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
  },
}));
