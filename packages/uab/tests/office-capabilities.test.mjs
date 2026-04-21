import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  buildExcelPivotTableScript,
  buildExcelChartScript,
  buildExcelConditionalFormattingScript,
  buildExcelWorkbookBenchmarkScript,
  resolveExcelWorkbookBenchmarkPaths,
  runExcelWorkbookBenchmark,
} from '../dist/plugins/office/index.js';

test('office plugin exposes a concrete PivotCaches implementation path', () => {
  const script = buildExcelPivotTableScript(1234, {
    sheet: 'Data',
    sourceRange: 'A1:D20',
    destinationSheet: 'Summary',
    destinationCell: 'F3',
    rowFields: ['Region'],
    dataField: 'Revenue',
  });

  assert.match(script, /PivotCaches\(\)\.Create/);
  assert.match(script, /CreatePivotTable/);
  assert.match(script, /\$pivot\.PivotFields\('Region'\)\.Orientation = 1/);
  assert.match(script, /\$valueField\.Orientation = 4/);
  assert.match(script, /\$valueField\.Function = -4157/);
});

test('office plugin exposes a concrete ChartObjects.Add implementation path', () => {
  const script = buildExcelChartScript(1234, {
    sheet: 'Summary',
    sourceRange: 'A1:B8',
    chartType: 'line',
    chartTitle: 'Revenue Trend',
  });

  assert.match(script, /ChartObjects\(\)\.Add/);
  assert.match(script, /\$chart\.SetSourceData/);
  assert.match(script, /ChartType = 4/);
  assert.match(script, /Revenue Trend/);
});

test('office plugin exposes a concrete FormatConditions implementation path', () => {
  const script = buildExcelConditionalFormattingScript(1234, {
    sheet: 'Summary',
    targetRange: 'B2:B20',
    formatType: 'dataBar',
  });

  assert.match(script, /FormatConditions\.Delete/);
  assert.match(script, /FormatConditions\.AddDatabar/);
  assert.match(script, /B2:B20/);
});

test('office plugin exposes a reproducible workbook benchmark harness', () => {
  const script = buildExcelWorkbookBenchmarkScript({
    rowCount: 250,
    outputPath: 'data/uab-benchmarks/test.xlsx',
  });
  const paths = resolveExcelWorkbookBenchmarkPaths({
    rowCount: 250,
    outputPath: 'data/uab-benchmarks/test.xlsx',
  });

  assert.match(script, /New-Object -ComObject Excel\.Application/);
  assert.match(script, /Workbooks\.Add/);
  assert.match(script, /PivotCaches\(\)\.Create/);
  assert.match(script, /ChartObjects\(\)\.Add/);
  assert.match(script, /FormatConditions\.AddColorScale/);
  assert.match(script, /SaveCopyAs/);
  assert.match(script, /Workbooks\.Open/);
  assert.match(script, /Persisted workbook artifact verification failed/);
  assert.match(paths.outputPath, /data[\\/]+uab-benchmarks[\\/]+test\.xlsx$/);
  assert.match(paths.manifestPath, /data[\\/]+uab-benchmarks[\\/]+test\.benchmark\.json$/);
});

if (process.platform === 'win32' && process.env.UAB_LIVE_OFFICE_TESTS === 'true') {
  test('office benchmark can build a live workbook when Excel is available', () => {
    const tempDir = mkdtempSync(path.join(os.tmpdir(), 'uab-office-live-'));
    const workbookPath = path.join(tempDir, 'live-test.xlsx');
    const manifestPath = path.join(tempDir, 'live-test.benchmark.json');
    const result = runExcelWorkbookBenchmark({
      rowCount: 250,
      outputPath: workbookPath,
      manifestPath,
    });

    try {
      assert.equal(result.success, true);
      assert.equal(result.rowCount, 250);
      assert.equal(result.pivotTableCount, 1);
      assert.equal(result.chartCount, 1);
      assert.ok(result.formatConditionCount >= 1);
      assert.equal(result.artifact?.verified, true);
      assert.equal(result.artifact?.pivotTableCount, 1);
      assert.equal(result.artifact?.chartCount, 1);
      assert.ok((result.artifact?.formatConditionCount || 0) >= 1);
      assert.match(result.workbookPath || '', /live-test\.xlsx$/);
      assert.match(result.manifestPath || '', /live-test\.benchmark\.json$/);
      assert.match(result.workbookSha256 || '', /^[a-f0-9]{64}$/);
      assert.equal(result.probe?.available, true);
    } finally {
      rmSync(tempDir, { recursive: true, force: true });
    }
  });
}
