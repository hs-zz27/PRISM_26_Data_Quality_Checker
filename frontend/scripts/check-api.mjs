import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const generator = resolve(frontendRoot, 'node_modules/openapi-typescript/bin/cli.js');
const tempRoot = mkdtempSync(join(tmpdir(), 'prism-api-types-'));
const outputs = [
  { contract: '../backend/api/openapi.json', generated: 'openapi.d.ts' },
];
let failed = false;

try {
  for (const item of outputs) {
    const contractPath = resolve(frontendRoot, item.contract);
    const expectedPath = resolve(frontendRoot, 'src/api/generated', item.generated);
    const actualPath = join(tempRoot, item.generated);
    execFileSync(process.execPath, [generator, contractPath, '-o', actualPath], { cwd: frontendRoot, stdio: 'inherit' });
    const expected = readFileSync(expectedPath, 'utf8');
    const actual = readFileSync(actualPath, 'utf8');
    if (expected !== actual) {
      console.error(`Generated API types are stale: src/api/generated/${item.generated}`);
      failed = true;
    } else {
      console.log(`API types current: src/api/generated/${item.generated}`);
    }
  }
} finally {
  rmSync(tempRoot, { recursive: true, force: true });
}

if (failed) {
  console.error('Run "npm run gen:api" and review the generated changes.');
  process.exitCode = 1;
}
