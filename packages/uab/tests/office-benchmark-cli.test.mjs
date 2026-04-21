import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';

test('cli exposes excel-benchmark dry-run output for reproducible workbook builds', () => {
  const result = spawnSync(
    process.execPath,
    ['dist/cli.js', 'excel-benchmark', '--rows', '250', '--output', 'data/uab-benchmarks/test.xlsx', '--manifest', 'data/uab-benchmarks/test-proof.json', '--dry-run'],
    {
      cwd: process.cwd(),
      encoding: 'utf8',
    },
  );

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const payload = JSON.parse(result.stdout);
  assert.equal(payload.dryRun, true);
  assert.equal(payload.options.rowCount, 250);
  assert.equal(payload.options.outputPath, 'data/uab-benchmarks/test.xlsx');
  assert.equal(payload.options.manifestPath, 'data/uab-benchmarks/test-proof.json');
  assert.match(payload.resolvedPaths.outputPath, /data[\\/]+uab-benchmarks[\\/]+test\.xlsx$/);
  assert.match(payload.resolvedPaths.manifestPath, /data[\\/]+uab-benchmarks[\\/]+test-proof\.json$/);
  assert.match(payload.script, /PivotCaches\(\)\.Create/);
  assert.match(payload.script, /ChartObjects\(\)\.Add/);
  assert.match(payload.script, /FormatConditions\.AddColorScale/);
  assert.match(payload.script, /verify-artifact/);
  assert.match(payload.script, /Workbooks\.Open/);
});

test('cli exposes excel-probe for local Office automation readiness', () => {
  const result = spawnSync(
    process.execPath,
    ['dist/cli.js', 'excel-probe'],
    {
      cwd: process.cwd(),
      encoding: 'utf8',
    },
  );

  assert.equal(result.status, 0, result.stderr || result.stdout);

  const payload = JSON.parse(result.stdout);
  assert.equal(typeof payload.available, 'boolean');
  if (payload.error !== undefined) {
    assert.equal(typeof payload.error, 'string');
  }
});
