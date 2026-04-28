/**
 * Excel COM automation helpers for the Office plugin.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import type { ActionParams } from '../../types.js';
import { runPSJson } from '../../ps-exec.js';

function escapePs(value: string): string {
  return value.replace(/'/g, "''");
}

function normalizeAggregation(aggregation?: ActionParams['aggregation']): string {
  switch ((aggregation || 'sum').toLowerCase()) {
    case 'count': return '-4112'; // xlCount
    case 'average': return '-4106'; // xlAverage
    case 'min': return '-4139'; // xlMin
    case 'max': return '-4136'; // xlMax
    case 'sum':
    default:
      return '-4157'; // xlSum
  }
}

function normalizeChartType(chartType?: ActionParams['chartType']): string {
  switch ((chartType || 'column').toLowerCase()) {
    case 'bar': return '57'; // xlBarClustered
    case 'line': return '4'; // xlLine
    case 'pie': return '5'; // xlPie
    case 'area': return '1'; // xlArea
    case 'column':
    default:
      return '51'; // xlColumnClustered
  }
}

function releaseComObject(targetVar: string): string {
  return `
if ($${targetVar}) {
  try { [void][System.Runtime.Interopservices.Marshal]::ReleaseComObject($${targetVar}) } catch {}
  $${targetVar} = $null
}
`;
}

export interface ExcelWorkbookBenchmarkOptions {
  rowCount?: number;
  outputPath?: string;
  manifestPath?: string;
  visible?: boolean;
  keepOpen?: boolean;
  dataSheetName?: string;
  summarySheetName?: string;
}

export interface ExcelWorkbookBenchmarkResult {
  success: boolean;
  elapsedMs: number;
  rowCount: number;
  workbookPath?: string;
  manifestPath?: string;
  workbookSha256?: string;
  sheetNames?: string[];
  pivotTableCount?: number;
  chartCount?: number;
  formatConditionCount?: number;
  keptOpen?: boolean;
  stage?: string;
  probe?: ExcelAutomationProbeResult;
  artifact?: {
    verified: boolean;
    fileSizeBytes?: number;
    sheetNames?: string[];
    pivotTableCount?: number;
    chartCount?: number;
    formatConditionCount?: number;
    error?: string;
  };
  error?: string;
}

export interface ExcelAutomationProbeResult {
  available: boolean;
  error?: string;
}

export interface ExcelWorkbookBenchmarkPaths {
  outputPath: string;
  manifestPath: string;
}

export interface ExcelWorkbookBenchmarkManifest {
  version: 1;
  generatedAt: string;
  host: {
    hostname: string;
    platform: string;
    release: string;
    arch: string;
    node: string;
  };
  probe: ExcelAutomationProbeResult;
  result: ExcelWorkbookBenchmarkResult;
}

interface ResolvedExcelWorkbookBenchmarkOptions {
  rowCount: number;
  outputPath: string;
  manifestPath: string;
  visible: boolean;
  keepOpen: boolean;
  dataSheetName: string;
  summarySheetName: string;
}

function defaultExcelWorkbookManifestPath(outputPath: string): string {
  const parsed = path.parse(outputPath);
  const stem = parsed.ext.toLowerCase() === '.xlsx' ? parsed.name : parsed.base;
  return path.join(parsed.dir, `${stem}.benchmark.json`);
}

function resolveExcelWorkbookBenchmarkOptions(
  options: ExcelWorkbookBenchmarkOptions = {},
): ResolvedExcelWorkbookBenchmarkOptions {
  const rowCount = Math.max(100, options.rowCount || 2000);
  const outputPath = path.resolve(options.outputPath || `data/uab-benchmarks/excel-benchmark-${Date.now()}.xlsx`);
  const manifestPath = path.resolve(options.manifestPath || defaultExcelWorkbookManifestPath(outputPath));
  return {
    rowCount,
    outputPath,
    manifestPath,
    visible: options.visible === true,
    keepOpen: options.keepOpen === true,
    dataSheetName: options.dataSheetName || 'Data',
    summarySheetName: options.summarySheetName || 'Summary',
  };
}

function hashFileSha256(filePath: string | undefined): string | undefined {
  if (!filePath || !existsSync(filePath)) {
    return undefined;
  }
  const digest = createHash('sha256');
  digest.update(readFileSync(filePath));
  return digest.digest('hex');
}

function writeJsonAtomically(filePath: string, payload: unknown): void {
  const dir = path.dirname(filePath);
  mkdirSync(dir, { recursive: true });
  const tempPath = `${filePath}.tmp-${process.pid}`;
  writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  try {
    renameSync(tempPath, filePath);
  } catch {
    try {
      unlinkSync(filePath);
    } catch {
      // Ignore replacement cleanup failures and let the rename retry surface real errors.
    }
    renameSync(tempPath, filePath);
  }
}

function buildExcelWorkbookBenchmarkManifest(
  result: ExcelWorkbookBenchmarkResult,
  probe: ExcelAutomationProbeResult,
): ExcelWorkbookBenchmarkManifest {
  return {
    version: 1,
    generatedAt: new Date().toISOString(),
    host: {
      hostname: os.hostname(),
      platform: process.platform,
      release: os.release(),
      arch: process.arch,
      node: process.version,
    },
    probe,
    result,
  };
}

export function resolveExcelWorkbookBenchmarkPaths(
  options: ExcelWorkbookBenchmarkOptions = {},
): ExcelWorkbookBenchmarkPaths {
  const resolved = resolveExcelWorkbookBenchmarkOptions(options);
  return {
    outputPath: resolved.outputPath,
    manifestPath: resolved.manifestPath,
  };
}

export function probeExcelAutomation(timeoutMs: number = 10000): ExcelAutomationProbeResult {
  const script = `
$ErrorActionPreference = 'Stop'
try {
  $xl = New-Object -ComObject Excel.Application
  $xl.DisplayAlerts = $false
  $xl.Quit()
  @{ available = $true } | ConvertTo-Json -Compress
} catch {
  @{ available = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;

  try {
    const result = runPSJson(script, timeoutMs) as Record<string, unknown>;
    return {
      available: result.available === true,
      error: typeof result.error === 'string' ? result.error : undefined,
    };
  } catch (err) {
    return {
      available: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export function buildExcelWorkbookBenchmarkScript(options: ExcelWorkbookBenchmarkOptions = {}): string {
  const resolved = resolveExcelWorkbookBenchmarkOptions(options);
  const outputPath = escapePs(resolved.outputPath);
  const dataSheetName = escapePs(resolved.dataSheetName);
  const summarySheetName = escapePs(resolved.summarySheetName);
  const visible = resolved.visible ? '$true' : '$false';
  const keepOpen = resolved.keepOpen ? '$true' : '$false';

  return `
$ErrorActionPreference = 'Stop'
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$outputPath = $null
$xl = $null
$wb = $null
$artifactXl = $null
$artifactWb = $null
$dataSheet = $null
$summarySheet = $null
$liveSheetNames = @()
$livePivotTableCount = 0
$liveChartCount = 0
$liveFormatConditionCount = 0
$keptOpenResult = $false
$currentStage = 'bootstrap'

try {
  $rowCount = ${resolved.rowCount}
  $outputPath = '${outputPath}'
  $outputDir = Split-Path -Parent $outputPath
  if ($outputDir) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
  }

  $xl = New-Object -ComObject Excel.Application
  $xl.Visible = ${visible}
  $xl.DisplayAlerts = $false
  $wb = $xl.Workbooks.Add()

  while ($wb.Worksheets.Count -lt 2) {
    $null = $wb.Worksheets.Add()
  }

  $currentStage = 'sheet-setup'
  $dataSheet = $wb.Worksheets.Item(1)
  $summarySheet = $wb.Worksheets.Item(2)
  $dataSheet.Name = '${dataSheetName}'
  $summarySheet.Name = '${summarySheetName}'

  $currentStage = 'seed-data'
  $rowTotal = [int]($rowCount + 1)
  $dimensionLengths = [int[]]($rowTotal, 4)
  $dimensionLowerBounds = [int[]](1, 1)
  $dataset = [System.Array]::CreateInstance([object], $dimensionLengths, $dimensionLowerBounds)
  $dataset[1, 1] = 'Region'
  $dataset[1, 2] = 'Quarter'
  $dataset[1, 3] = 'Owner'
  $dataset[1, 4] = 'Revenue'

  $regions = @('North', 'South', 'East', 'West')
  $quarters = @('Q1', 'Q2', 'Q3', 'Q4')
  $owners = @('Avery', 'Blake', 'Casey', 'Dakota')

  for ($i = 2; $i -le $rowCount + 1; $i++) {
    $ordinal = $i - 2
    $dataset[$i, 1] = $regions[$ordinal % $regions.Count]
    $dataset[$i, 2] = $quarters[[int][Math]::Floor($ordinal / $regions.Count) % $quarters.Count]
    $dataset[$i, 3] = $owners[$ordinal % $owners.Count]
    $dataset[$i, 4] = 1000 + (($ordinal % 12) * 275)
  }

  $dataRange = $dataSheet.Range("A1:D$($rowCount + 1)")
  $dataRange.Value2 = $dataset
  $headerRange = $dataSheet.Range('A1:D1')
  $headerRange.Font.Bold = $true
  $headerRange.Interior.Color = 15773696
  $dataSheet.Range("D2:D$($rowCount + 1)").NumberFormat = '$#,##0'
  $null = $dataSheet.Columns.AutoFit()

  $currentStage = 'pivot-table'
  $cache = $wb.PivotCaches().Create(1, $dataRange)
  $pivot = $cache.CreatePivotTable($summarySheet.Range('F3'), 'UABBenchmarkPivot')
  $pivot.PivotFields('Region').Orientation = 1
  $pivot.PivotFields('Region').Position = 1
  $pivot.PivotFields('Quarter').Orientation = 2
  $pivot.PivotFields('Quarter').Position = 1
  $valueField = $pivot.PivotFields('Revenue')
  $valueField.Orientation = 4
  $valueField.Function = -4157

  $currentStage = 'chart'
  $chartObject = $summarySheet.ChartObjects().Add(240, 20, 420, 260)
  $chart = $chartObject.Chart
  $chart.SetSourceData($summarySheet.Range('F3:J7'))
  $chart.ChartType = 51
  $chart.HasTitle = $true
  $chart.ChartTitle.Text = 'Regional Revenue Benchmark'

  $currentStage = 'conditional-formatting'
  $formatRange = $summarySheet.Range('G4:J7')
  $formatRange.FormatConditions.Delete()
  $null = $formatRange.FormatConditions.AddColorScale(3)
  $null = $summarySheet.Columns.AutoFit()

  $currentStage = 'save-artifact'
  $wb.SaveCopyAs($outputPath)
  $liveSheetNames = @($dataSheet.Name, $summarySheet.Name)
  $livePivotTableCount = $summarySheet.PivotTables().Count
  $liveChartCount = $summarySheet.ChartObjects().Count
  $liveFormatConditionCount = $formatRange.FormatConditions.Count
  ${releaseComObject('formatRange').trim()}
  ${releaseComObject('chart').trim()}
  ${releaseComObject('chartObject').trim()}
  ${releaseComObject('valueField').trim()}
  ${releaseComObject('pivot').trim()}
  ${releaseComObject('cache').trim()}
  ${releaseComObject('headerRange').trim()}
  ${releaseComObject('dataRange').trim()}
  ${releaseComObject('dataSheet').trim()}
  ${releaseComObject('summarySheet').trim()}

  if ($wb) {
    try { $wb.Close($false) } catch {}
  }
  ${releaseComObject('wb').trim()}
  if ($xl) {
    try { $xl.Quit() } catch {}
  }
  ${releaseComObject('xl').trim()}
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
  Start-Sleep -Milliseconds 500

  $currentStage = 'verify-artifact'
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [System.IO.Compression.ZipFile]::OpenRead($outputPath)
  $artifactSheetNames = @()
  $artifactPivotTableCount = 0
  $artifactChartCount = 0
  $artifactFormatConditionCount = 0
  try {
    foreach ($entry in $archive.Entries) {
      if ($entry.FullName -like 'xl/pivotTables/pivotTable*.xml') {
        $artifactPivotTableCount++
      } elseif ($entry.FullName -like 'xl/charts/chart*.xml') {
        $artifactChartCount++
      } elseif ($entry.FullName -like 'xl/worksheets/sheet*.xml') {
        $reader = New-Object System.IO.StreamReader($entry.Open())
        try {
          $sheetXml = $reader.ReadToEnd()
        } finally {
          $reader.Dispose()
        }
        $artifactFormatConditionCount += ([regex]::Matches($sheetXml, '<conditionalFormatting\\b')).Count
      } elseif ($entry.FullName -eq 'xl/workbook.xml') {
        $reader = New-Object System.IO.StreamReader($entry.Open())
        try {
          $workbookXml = [xml]$reader.ReadToEnd()
        } finally {
          $reader.Dispose()
        }
        foreach ($sheetNode in $workbookXml.SelectNodes("//*[local-name()='sheet']")) {
          $artifactSheetNames += $sheetNode.GetAttribute('name')
        }
      }
    }
  } finally {
    $archive.Dispose()
  }
  $artifactFileSizeBytes = (Get-Item $outputPath).Length

  if ($artifactPivotTableCount -lt 1 -or $artifactChartCount -lt 1 -or $artifactFormatConditionCount -lt 1) {
    throw "Persisted workbook artifact verification failed"
  }

  if (${keepOpen}) {
    $currentStage = 'reopen-artifact'
    $artifactXl = New-Object -ComObject Excel.Application
    $artifactXl.Visible = ${visible}
    $artifactXl.DisplayAlerts = $false
    $artifactWb = $artifactXl.Workbooks.Open($outputPath)
    $keptOpenResult = $true
    $artifactWb = $null
    $artifactXl = $null
  }

  $stopwatch.Stop()

  @{
    success = $true
    elapsedMs = $stopwatch.ElapsedMilliseconds
    rowCount = $rowCount
    workbookPath = $outputPath
    sheetNames = $liveSheetNames
    pivotTableCount = $livePivotTableCount
    chartCount = $liveChartCount
    formatConditionCount = $liveFormatConditionCount
    keptOpen = $keptOpenResult
    stage = $currentStage
    artifact = @{
      verified = $true
      fileSizeBytes = $artifactFileSizeBytes
      sheetNames = $artifactSheetNames
      pivotTableCount = $artifactPivotTableCount
      chartCount = $artifactChartCount
      formatConditionCount = $artifactFormatConditionCount
    }
  } | ConvertTo-Json -Compress -Depth 4
} catch {
  $stopwatch.Stop()
  @{
    success = $false
    elapsedMs = $stopwatch.ElapsedMilliseconds
    rowCount = ${resolved.rowCount}
    workbookPath = $outputPath
    stage = $currentStage
    artifact = @{
      verified = $false
      error = $_.Exception.Message
    }
    error = $_.Exception.Message
  } | ConvertTo-Json -Compress -Depth 4
} finally {
  if ($artifactWb) {
    try { $artifactWb.Close($false) } catch {}
  }
  ${releaseComObject('artifactWb').trim()}
  if ($artifactXl) {
    try { $artifactXl.Quit() } catch {}
  }
  ${releaseComObject('artifactXl').trim()}
  if (-not ${keepOpen}) {
    if ($wb) {
      try { $wb.Close($false) } catch {}
    }
    ${releaseComObject('wb').trim()}
    if ($xl) {
      try { $xl.Quit() } catch {}
    }
    ${releaseComObject('xl').trim()}
  }
}
`;
}

export function runExcelWorkbookBenchmark(options: ExcelWorkbookBenchmarkOptions = {}): ExcelWorkbookBenchmarkResult {
  const resolved = resolveExcelWorkbookBenchmarkOptions(options);
  const probe = probeExcelAutomation();
  if (!probe.available) {
    const result: ExcelWorkbookBenchmarkResult = {
      success: false,
      elapsedMs: 0,
      rowCount: resolved.rowCount,
      workbookPath: resolved.outputPath,
      manifestPath: resolved.manifestPath,
      stage: 'probe',
      probe,
      error: `Excel COM automation unavailable: ${probe.error || 'probe failed'}`,
    };
    try {
      writeJsonAtomically(resolved.manifestPath, buildExcelWorkbookBenchmarkManifest(result, probe));
    } catch (err) {
      return {
        ...result,
        success: false,
        stage: 'persist-manifest',
        error: `Excel COM automation unavailable: ${probe.error || 'probe failed'}; benchmark manifest write failed: ${err instanceof Error ? err.message : String(err)}`,
      };
    }
    return result;
  }

  try {
    const raw = runPSJson(buildExcelWorkbookBenchmarkScript(resolved), 120000) as Record<string, unknown>;
    const result: ExcelWorkbookBenchmarkResult = {
      success: raw.success === true,
      elapsedMs: typeof raw.elapsedMs === 'number' ? raw.elapsedMs : Number(raw.elapsedMs || 0),
      rowCount: typeof raw.rowCount === 'number' ? raw.rowCount : Number(raw.rowCount || resolved.rowCount),
      workbookPath: typeof raw.workbookPath === 'string' ? raw.workbookPath : resolved.outputPath,
      manifestPath: resolved.manifestPath,
      workbookSha256: hashFileSha256(typeof raw.workbookPath === 'string' ? raw.workbookPath : resolved.outputPath),
      sheetNames: Array.isArray(raw.sheetNames) ? raw.sheetNames.map((value) => String(value)) : undefined,
      pivotTableCount: typeof raw.pivotTableCount === 'number' ? raw.pivotTableCount : Number(raw.pivotTableCount || 0),
      chartCount: typeof raw.chartCount === 'number' ? raw.chartCount : Number(raw.chartCount || 0),
      formatConditionCount: typeof raw.formatConditionCount === 'number' ? raw.formatConditionCount : Number(raw.formatConditionCount || 0),
      keptOpen: raw.keptOpen === true,
      stage: typeof raw.stage === 'string' ? raw.stage : undefined,
      probe,
      artifact: typeof raw.artifact === 'object' && raw.artifact
        ? {
            verified: (raw.artifact as Record<string, unknown>).verified === true,
            fileSizeBytes: typeof (raw.artifact as Record<string, unknown>).fileSizeBytes === 'number'
              ? (raw.artifact as Record<string, unknown>).fileSizeBytes as number
              : Number((raw.artifact as Record<string, unknown>).fileSizeBytes || 0) || undefined,
            sheetNames: Array.isArray((raw.artifact as Record<string, unknown>).sheetNames)
              ? ((raw.artifact as Record<string, unknown>).sheetNames as unknown[]).map((value) => String(value))
              : undefined,
            pivotTableCount: typeof (raw.artifact as Record<string, unknown>).pivotTableCount === 'number'
              ? (raw.artifact as Record<string, unknown>).pivotTableCount as number
              : Number((raw.artifact as Record<string, unknown>).pivotTableCount || 0) || undefined,
            chartCount: typeof (raw.artifact as Record<string, unknown>).chartCount === 'number'
              ? (raw.artifact as Record<string, unknown>).chartCount as number
              : Number((raw.artifact as Record<string, unknown>).chartCount || 0) || undefined,
            formatConditionCount: typeof (raw.artifact as Record<string, unknown>).formatConditionCount === 'number'
              ? (raw.artifact as Record<string, unknown>).formatConditionCount as number
              : Number((raw.artifact as Record<string, unknown>).formatConditionCount || 0) || undefined,
            error: typeof (raw.artifact as Record<string, unknown>).error === 'string'
              ? (raw.artifact as Record<string, unknown>).error as string
              : undefined,
          }
        : undefined,
      error: typeof raw.error === 'string' ? raw.error : undefined,
    };
    try {
      writeJsonAtomically(resolved.manifestPath, buildExcelWorkbookBenchmarkManifest(result, probe));
    } catch (err) {
      return {
        ...result,
        success: false,
        stage: 'persist-manifest',
        error: `Benchmark manifest write failed: ${err instanceof Error ? err.message : String(err)}`,
      };
    }
    return result;
  } catch (err) {
    const result: ExcelWorkbookBenchmarkResult = {
      success: false,
      elapsedMs: 0,
      rowCount: resolved.rowCount,
      workbookPath: resolved.outputPath,
      manifestPath: resolved.manifestPath,
      stage: 'execute',
      probe,
      error: err instanceof Error ? err.message : String(err),
    };
    try {
      writeJsonAtomically(resolved.manifestPath, buildExcelWorkbookBenchmarkManifest(result, probe));
    } catch (manifestErr) {
      return {
        ...result,
        success: false,
        stage: 'persist-manifest',
        error: `${result.error}; benchmark manifest write failed: ${manifestErr instanceof Error ? manifestErr.message : String(manifestErr)}`,
      };
    }
    return result;
  }
}

export function buildExcelPivotTableScript(pid: number, params?: ActionParams): string {
  const sheet = escapePs(params?.sheet || '');
  const sourceRange = escapePs(params?.sourceRange || params?.cellRange || 'A1:D10');
  const destinationSheet = escapePs(params?.destinationSheet || sheet || 'Sheet1');
  const destinationCell = escapePs(params?.destinationCell || 'F3');
  const dataField = escapePs(params?.dataField || 'Value');
  const rowFields = (params?.rowFields || []).map(escapePs);
  const columnFields = (params?.columnFields || []).map(escapePs);
  const aggregation = normalizeAggregation(params?.aggregation);

  return `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $sourceSheet = if ('${sheet}') { $wb.Sheets.Item('${sheet}') } else { $xl.ActiveSheet }
  $targetSheet = $wb.Sheets.Item('${destinationSheet}')
  $source = $sourceSheet.Range('${sourceRange}')
  $cache = $wb.PivotCaches().Create(1, $source)
  $pivot = $cache.CreatePivotTable($targetSheet.Range('${destinationCell}'), 'UABPivotTable')
  ${rowFields.map((field, index) => `$pivot.PivotFields('${field}').Orientation = 1; $pivot.PivotFields('${field}').Position = ${index + 1}`).join("\n  ")}
  ${columnFields.map((field, index) => `$pivot.PivotFields('${field}').Orientation = 2; $pivot.PivotFields('${field}').Position = ${index + 1}`).join("\n  ")}
  $valueField = $pivot.PivotFields('${dataField}')
  $valueField.Orientation = 4
  $valueField.Function = ${aggregation}
  @{ success = $true; destinationSheet = $targetSheet.Name; destinationCell = '${destinationCell}'; rowFieldCount = ${rowFields.length}; columnFieldCount = ${columnFields.length} } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
}

export function buildExcelChartScript(pid: number, params?: ActionParams): string {
  const sheet = escapePs(params?.sheet || '');
  const sourceRange = escapePs(params?.sourceRange || params?.cellRange || 'A1:B10');
  const chartType = normalizeChartType(params?.chartType);
  const title = escapePs(params?.chartTitle || 'UAB Chart');

  return `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $ws = if ('${sheet}') { $wb.Sheets.Item('${sheet}') } else { $xl.ActiveSheet }
  $source = $ws.Range('${sourceRange}')
  $chartObject = $ws.ChartObjects().Add(240, 20, 420, 260)
  $chart = $chartObject.Chart
  $chart.SetSourceData($source)
  $chart.ChartType = ${chartType}
  $chart.HasTitle = $true
  $chart.ChartTitle.Text = '${title}'
  @{ success = $true; sheet = $ws.Name; sourceRange = '${sourceRange}'; chartTitle = '${title}' } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
}

export function buildExcelConditionalFormattingScript(pid: number, params?: ActionParams): string {
  const sheet = escapePs(params?.sheet || '');
  const targetRange = escapePs(params?.targetRange || params?.cellRange || 'A1:A10');
  const formatType = params?.formatType || 'colorScale';

  const formatScript = formatType === 'dataBar'
    ? '$null = $rng.FormatConditions.AddDatabar()'
    : formatType === 'iconSet'
      ? '$null = $rng.FormatConditions.AddIconSetCondition()'
      : '$null = $rng.FormatConditions.AddColorScale(3)';

  return `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $ws = if ('${sheet}') { $wb.Sheets.Item('${sheet}') } else { $xl.ActiveSheet }
  $rng = $ws.Range('${targetRange}')
  $rng.FormatConditions.Delete()
  ${formatScript}
  @{ success = $true; sheet = $ws.Name; targetRange = '${targetRange}'; formatType = '${formatType}' } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
}

// ─── Office Plugin ─────────────────────────────────────────
