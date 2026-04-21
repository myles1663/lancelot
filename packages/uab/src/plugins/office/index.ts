/**
 * Microsoft Office Framework Plugin
 *
 * Provides deep integration with Office apps (Word, Excel, PowerPoint,
 * Outlook) by combining UIA accessibility with Office-specific
 * capabilities:
 *
 *   - Document content reading via UIA TextPattern
 *   - Excel cell/range reading via UIA GridPattern + ValuePattern
 *   - Excel cell writing via UIA ValuePattern
 *   - Office Ribbon navigation with friendly names
 *   - Smart element labeling for Office-specific controls
 *
 * Detection: WINWORD.EXE, EXCEL.EXE, POWERPNT.EXE, OUTLOOK.EXE, etc.
 * Falls back to Win-UIA for standard UI actions.
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import {
  FrameworkPlugin,
  PluginConnection,
  DetectedApp,
  UIElement,
  ElementSelector,
  ActionType,
  ActionParams,
  ActionResult,
  AppState,
  UABEventType,
  UABEventCallback,
  Subscription,
} from '../../types.js';
import { runPSJson, runPSJsonInteractive, runPSRawInteractive } from '../../ps-exec.js';
import { WinUIAPlugin } from '../win-uia/index.js';

// ─── Office App Type Identification ─────────────────────────

type OfficeAppType = 'word' | 'excel' | 'powerpoint' | 'outlook' | 'onenote' | 'access' | 'other';

const OFFICE_PROCESS_MAP: Record<string, OfficeAppType> = {
  'winword.exe': 'word',
  'winword': 'word',
  'excel.exe': 'excel',
  'excel': 'excel',
  'powerpnt.exe': 'powerpoint',
  'powerpnt': 'powerpoint',
  'outlook.exe': 'outlook',
  'outlook': 'outlook',
  'onenote.exe': 'onenote',
  'onenote': 'onenote',
  'msaccess.exe': 'access',
  'msaccess': 'access',
};

function identifyOfficeApp(app: DetectedApp): OfficeAppType {
  const name = app.name.toLowerCase();
  return OFFICE_PROCESS_MAP[name] || OFFICE_PROCESS_MAP[name + '.exe'] || 'other';
}

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

export class OfficePlugin implements FrameworkPlugin {
  readonly framework = 'office' as const;
  readonly name = 'Microsoft Office (UIA + Office Patterns)';
  readonly controlMethod = 'office-com+uia' as const;
  private uiaPlugin = new WinUIAPlugin();

  canHandle(app: DetectedApp): boolean {
    return app.framework === 'office';
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const uiaConn = await this.uiaPlugin.connect(app);
    const officeType = identifyOfficeApp(app);
    return new OfficeConnection(app, uiaConn, officeType);
  }
}

// ─── Office Connection ─────────────────────────────────────

class OfficeConnection implements PluginConnection {
  readonly app: DetectedApp;
  private uiaConn: PluginConnection;
  private officeType: OfficeAppType;

  constructor(app: DetectedApp, uiaConn: PluginConnection, officeType: OfficeAppType) {
    this.app = app;
    this.uiaConn = uiaConn;
    this.officeType = officeType;
  }

  get connected(): boolean { return this.uiaConn.connected; }

  async enumerate(): Promise<UIElement[]> {
    const elements = await this.uiaConn.enumerate();
    return elements.map(el => this.enhanceOfficeElement(el));
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const elements = await this.uiaConn.query(selector);
    return elements.map(el => this.enhanceOfficeElement(el));
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    // Handle Office-specific actions
    switch (action) {
      case 'readDocument':
        return this.readDocumentContent();
      case 'readCell':
        return this.readExcelCell(params);
      case 'writeCell':
        return this.writeExcelCell(params);
      // Excel COM actions
      case 'readRange':
        return this.comReadRange(params);
      case 'writeRange':
        return this.comWriteRange(params);
      case 'getSheets':
        return this.comGetSheets();
      case 'readFormula':
        return this.comReadFormula(params);
      case 'createPivotTable':
        return this.comCreatePivotTable(params);
      case 'createChart':
        return this.comCreateChart(params);
      case 'applyConditionalFormatting':
        return this.comApplyConditionalFormatting(params);
      // Outlook COM actions
      case 'readEmails':
        return this.comReadEmails(params);
      case 'composeEmail':
        return this.comComposeEmail(params);
      case 'sendEmail':
        return this.comSendEmail(params);
      // PowerPoint COM actions
      case 'readSlides':
        return this.comReadSlides();
      case 'readSlideText':
        return this.comReadSlideText(params);
    }

    // Delegate everything else to UIA
    return this.uiaConn.act(elementId, action, params);
  }

  async state(): Promise<AppState> {
    const baseState = await this.uiaConn.state();
    return {
      ...baseState,
      window: {
        ...baseState.window,
        title: `[${this.officeType.toUpperCase()}] ${baseState.window.title}`,
      },
    };
  }

  async subscribe(event: UABEventType, callback: UABEventCallback): Promise<Subscription> {
    return this.uiaConn.subscribe(event, callback);
  }

  async disconnect(): Promise<void> {
    return this.uiaConn.disconnect();
  }

  // ─── Office Element Enhancement ───────────────────────────

  private enhanceOfficeElement(el: UIElement): UIElement {
    const className = (el.properties.className as string) || '';
    const automationId = (el.properties.automationId as string) || '';
    const controlType = (el.properties.controlType as string) || '';

    // Identify Office-specific UI regions
    let officeRole: string | undefined;

    if (className.includes('NetUIRibbonTab') || automationId.includes('Ribbon')) {
      officeRole = 'ribbon';
    } else if (className.includes('_WwG') || className === 'RICHEDIT60W' || className === 'RICHEDIT50W') {
      officeRole = 'document-body';
    } else if (className === 'XLMAIN' || className.includes('EXCEL')) {
      officeRole = 'spreadsheet';
    } else if (className.includes('NetUIToolWindow')) {
      officeRole = 'tool-pane';
    } else if (controlType === 'StatusBar') {
      officeRole = 'status-bar';
    } else if (className.includes('MsoCommandBar') || automationId.includes('QAT')) {
      officeRole = 'quick-access-toolbar';
    }

    // Add Office-specific actions based on role
    let actions = [...el.actions];
    if (officeRole === 'document-body') {
      actions.push('readDocument');
    }
    if (officeRole === 'spreadsheet' || this.officeType === 'excel') {
      if (controlType === 'DataItem' || controlType === 'Edit' || controlType === 'Custom') {
        actions.push('readCell', 'writeCell');
      }
      // COM-based Excel actions available at any element level
      actions.push('readRange', 'writeRange', 'getSheets', 'readFormula', 'createPivotTable', 'createChart', 'applyConditionalFormatting');
    }
    if (this.officeType === 'outlook') {
      actions.push('readEmails', 'composeEmail', 'sendEmail');
    }
    if (this.officeType === 'powerpoint') {
      actions.push('readSlides', 'readSlideText');
    }

    return {
      ...el,
      actions,
      meta: {
        ...el.meta,
        pluginSource: 'office',
        officeApp: this.officeType,
        officeRole,
      },
      children: el.children.map(c => this.enhanceOfficeElement(c)),
    };
  }

  // ─── Document Content Reading (Word) ──────────────────────

  private async readDocumentContent(): Promise<ActionResult> {
    if (this.officeType !== 'word') {
      return { success: false, error: `readDocument is only supported for Word (current: ${this.officeType})` };
    }

    try {
      const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ErrorActionPreference = 'SilentlyContinue'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${this.app.pid}
)
$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$text = ''
foreach ($win in $appWindows) {
  # Find the document pane — Word uses _WwG class for the editing area
  $docCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Document
  )
  $docs = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $docCond)
  foreach ($doc in $docs) {
    try {
      $textPattern = $doc.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
      if ($textPattern) {
        $docRange = $textPattern.DocumentRange
        $text = $docRange.GetText(-1)
        break
      }
    } catch { }
  }
  if ($text) { break }

  # Fallback: try any element with TextPattern
  $allCond = [System.Windows.Automation.Condition]::TrueCondition
  $all = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $allCond)
  foreach ($el in $all) {
    try {
      $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
      if ($tp) {
        $r = $tp.DocumentRange
        $content = $r.GetText(-1)
        if ($content.Length -gt $text.Length) { $text = $content }
      }
    } catch { }
  }
}

@{ success = $true; text = $text; length = $text.Length } | ConvertTo-Json -Compress
`;
      const result = runPSJsonInteractive(script, 30000) as { success: boolean; text: string; length: number };
      return {
        success: true,
        result: {
          text: result.text,
          length: result.length,
          app: 'word',
        },
      };
    } catch (err) {
      return { success: false, error: `Failed to read document: ${err}` };
    }
  }

  // ─── Excel Cell Reading ───────────────────────────────────

  private async readExcelCell(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `readCell is only supported for Excel (current: ${this.officeType})` };
    }

    const cellRange = params?.cellRange || (params?.row && params?.col ? null : 'A1');
    const row = params?.row;
    const col = params?.col;

    try {
      let script: string;

      if (cellRange) {
        // Read a cell range like "A1" or "A1:C5" using the Name Box + keyboard
        script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ErrorActionPreference = 'SilentlyContinue'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${this.app.pid}
)
$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$cells = @()
foreach ($win in $appWindows) {
  # Find the Excel spreadsheet grid (AutomationId='Grid', Class='XLSpreadsheetGrid')
  $gridCond = New-Object System.Windows.Automation.AndCondition(
    (New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
      [System.Windows.Automation.ControlType]::DataGrid)),
    (New-Object System.Windows.Automation.PropertyCondition(
      [System.Windows.Automation.AutomationElement]::AutomationIdProperty, 'Grid'))
  )
  $grids = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $gridCond)
  foreach ($grid in $grids) {
    try {
      $gridPattern = $grid.GetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern)
      if ($gridPattern) {
        # Parse range: could be "A1" or "A1:C5"
        $range = '${(cellRange || 'A1').replace(/'/g, "''")}'
        $parts = $range -split ':'

        function Parse-CellRef($ref) {
          $ref = $ref.Trim().ToUpper()
          $colStr = ($ref -replace '[0-9]', '')
          $rowStr = ($ref -replace '[A-Z]', '')
          $colNum = 0
          foreach ($ch in $colStr.ToCharArray()) {
            $colNum = $colNum * 26 + ([int][char]$ch - 64)
          }
          # Excel UIA grid has headers at row 0 / col 0
          # So A1 = GetItem(1, 1), B2 = GetItem(2, 2), etc.
          return @{ Row = [int]$rowStr; Col = $colNum }
        }

        $start = Parse-CellRef $parts[0]
        if ($parts.Count -gt 1) {
          $end = Parse-CellRef $parts[1]
        } else {
          $end = $start
        }

        for ($r = $start.Row; $r -le $end.Row; $r++) {
          for ($c = $start.Col; $c -le $end.Col; $c++) {
            try {
              $item = $gridPattern.GetItem($r, $c)
              if ($item) {
                $name = $item.Current.Name
                $val = ''
                try {
                  $vp = $item.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                  if ($vp) { $val = $vp.Current.Value }
                } catch { $val = $name }
                if (-not $val) { $val = $name }
                $cells += @{
                  row = $r
                  col = $c
                  value = $val
                  name = $name
                }
              }
            } catch { }
          }
        }
        break
      }
    } catch { }
  }
  if ($cells.Count -gt 0) { break }
}

@{ success = $true; cells = $cells; count = $cells.Count } | ConvertTo-Json -Depth 5 -Compress
`;
      } else {
        // Read by row/col number
        script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ErrorActionPreference = 'SilentlyContinue'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${this.app.pid}
)
$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$value = $null
foreach ($win in $appWindows) {
  $gridCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::DataGrid
  )
  $grids = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $gridCond)
  foreach ($grid in $grids) {
    try {
      $gridPattern = $grid.GetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern)
      if ($gridPattern) {
        $item = $gridPattern.GetItem(${row || 1}, ${col || 1})
        if ($item) {
          $name = $item.Current.Name
          try {
            $vp = $item.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            if ($vp) { $value = $vp.Current.Value }
          } catch { $value = $name }
          if (-not $value) { $value = $name }
        }
        break
      }
    } catch { }
  }
  if ($null -ne $value) { break }
}

@{ success = $true; row = ${row || 1}; col = ${col || 1}; value = $value } | ConvertTo-Json -Compress
`;
      }

      const result = runPSJsonInteractive(script, 30000) as Record<string, unknown>;
      return { success: true, result };
    } catch (err) {
      return { success: false, error: `Failed to read cell: ${err}` };
    }
  }

  // ─── Excel COM: Read Range (batch) ───────────────────────

  private async comReadRange(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `readRange is only supported for Excel (current: ${this.officeType})` };
    }
    const range = params?.cellRange || 'A1:A1';
    const sheet = params?.sheet || '';
    try {
      const escapedSheet = sheet.replace(/'/g, "''");
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $ws = if ('${escapedSheet}') { $wb.Sheets.Item('${escapedSheet}') } else { $xl.ActiveSheet }
  $rng = $ws.Range('${range.replace(/'/g, "''")}')
  $rows = $rng.Rows.Count
  $cols = $rng.Columns.Count
  $data = @()
  for ($r = 1; $r -le $rows; $r++) {
    $rowData = @()
    for ($c = 1; $c -le $cols; $c++) {
      $cell = $rng.Cells.Item($r, $c)
      $rowData += @{
        value = [string]$cell.Value2
        formula = [string]$cell.Formula
        address = [string]$cell.Address($false, $false)
      }
    }
    $data += ,@($rowData)
  }
  @{ success = $true; range = '${range.replace(/'/g, "''")}'; sheet = $ws.Name; rows = $rows; cols = $cols; data = $data } | ConvertTo-Json -Depth 5 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 30000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM readRange failed: ${err}` };
    }
  }

  // ─── Excel COM: Write Range (batch) ──────────────────────

  private async comWriteRange(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `writeRange is only supported for Excel (current: ${this.officeType})` };
    }
    const range = params?.cellRange || 'A1';
    const sheet = params?.sheet || '';
    const values = params?.values;
    const text = params?.text;
    const formula = params?.formula;
    try {
      const escapedSheet = sheet.replace(/'/g, "''");
      let valueScript: string;
      if (formula) {
        valueScript = `$rng.Formula = '${formula.replace(/'/g, "''")}'`;
      } else if (values && values.length > 0) {
        const rows = values.length;
        const cols = Math.max(...values.map(r => r.length));
        const arrayLiteral = values.map(row =>
          row.map(v => `'${(v || '').replace(/'/g, "''")}'`).join(',')
        ).join('),(');
        valueScript = `
$arr = New-Object 'object[,]' ${rows},${cols}
$srcRows = @(,@(${arrayLiteral}))
for ($r = 0; $r -lt ${rows}; $r++) {
  for ($c = 0; $c -lt $srcRows[$r].Count; $c++) {
    $arr[$r,$c] = $srcRows[$r][$c]
  }
}
$rng.Value2 = $arr`;
      } else if (text) {
        valueScript = `$rng.Value2 = '${text.replace(/'/g, "''")}'`;
      } else {
        return { success: false, error: 'No values, text, or formula provided for writeRange' };
      }

      const script = `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $ws = if ('${escapedSheet}') { $wb.Sheets.Item('${escapedSheet}') } else { $xl.ActiveSheet }
  $rng = $ws.Range('${range.replace(/'/g, "''")}')
  ${valueScript}
  @{ success = $true; range = '${range.replace(/'/g, "''")}'; sheet = $ws.Name } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM writeRange failed: ${err}` };
    }
  }

  // ─── Excel COM: Get Sheet Names ──────────────────────────

  private async comGetSheets(): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `getSheets is only supported for Excel (current: ${this.officeType})` };
    }
    try {
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $sheets = @()
  foreach ($ws in $wb.Sheets) {
    $sheets += @{
      name = $ws.Name
      index = $ws.Index
      visible = ($ws.Visible -eq -1)
      usedRange = $ws.UsedRange.Address($false, $false)
      rowCount = $ws.UsedRange.Rows.Count
      colCount = $ws.UsedRange.Columns.Count
    }
  }
  $active = $xl.ActiveSheet.Name
  @{ success = $true; sheets = $sheets; activeSheet = $active; count = $sheets.Count } | ConvertTo-Json -Depth 4 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM getSheets failed: ${err}` };
    }
  }

  // ─── Excel COM: Read Formula ─────────────────────────────

  private async comReadFormula(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `readFormula is only supported for Excel (current: ${this.officeType})` };
    }
    const range = params?.cellRange || 'A1';
    const sheet = params?.sheet || '';
    try {
      const escapedSheet = sheet.replace(/'/g, "''");
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $xl = [Runtime.Interopservices.Marshal]::GetActiveObject('Excel.Application')
  $wb = $xl.ActiveWorkbook
  if (-not $wb) { throw 'No active workbook' }
  $ws = if ('${escapedSheet}') { $wb.Sheets.Item('${escapedSheet}') } else { $xl.ActiveSheet }
  $rng = $ws.Range('${range.replace(/'/g, "''")}')
  $cells = @()
  foreach ($cell in $rng) {
    $cells += @{
      address = $cell.Address($false, $false)
      value = [string]$cell.Value2
      formula = [string]$cell.Formula
      formulaLocal = [string]$cell.FormulaLocal
      hasFormula = $cell.HasFormula
      numberFormat = [string]$cell.NumberFormat
    }
  }
  @{ success = $true; cells = $cells; count = $cells.Count } | ConvertTo-Json -Depth 4 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM readFormula failed: ${err}` };
    }
  }

  // ─── Outlook COM: Read Emails ────────────────────────────

  private async comCreatePivotTable(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `createPivotTable is only supported for Excel (current: ${this.officeType})` };
    }
    try {
      const result = runPSJsonInteractive(buildExcelPivotTableScript(this.app.pid, params), 20000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM createPivotTable failed: ${err}` };
    }
  }

  private async comCreateChart(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `createChart is only supported for Excel (current: ${this.officeType})` };
    }
    try {
      const result = runPSJsonInteractive(buildExcelChartScript(this.app.pid, params), 20000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM createChart failed: ${err}` };
    }
  }

  private async comApplyConditionalFormatting(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `applyConditionalFormatting is only supported for Excel (current: ${this.officeType})` };
    }
    try {
      const result = runPSJsonInteractive(buildExcelConditionalFormattingScript(this.app.pid, params), 20000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM applyConditionalFormatting failed: ${err}` };
    }
  }

  private async comReadEmails(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'outlook') {
      return { success: false, error: `readEmails is only supported for Outlook (current: ${this.officeType})` };
    }
    const folder = params?.folder || 'Inbox';
    const count = params?.count || 10;
    try {
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $ol = [Runtime.Interopservices.Marshal]::GetActiveObject('Outlook.Application')
  $ns = $ol.GetNamespace('MAPI')

  # Map folder names to Outlook constants
  $folderMap = @{
    'Inbox' = 6; 'Outbox' = 4; 'Sent' = 5; 'Drafts' = 16;
    'Deleted' = 3; 'Junk' = 23; 'Calendar' = 9; 'Contacts' = 10
  }
  $folderId = $folderMap['${folder}']
  if ($folderId) {
    $fldr = $ns.GetDefaultFolder($folderId)
  } else {
    $fldr = $ns.GetDefaultFolder(6) # fallback to Inbox
  }

  $items = $fldr.Items
  $items.Sort('[ReceivedTime]', $true)
  $emails = @()
  $max = [Math]::Min(${count}, $items.Count)
  for ($i = 1; $i -le $max; $i++) {
    $msg = $items.Item($i)
    if ($msg.Class -eq 43) { # olMail
      $emails += @{
        subject = $msg.Subject
        from = $msg.SenderName
        to = $msg.To
        received = $msg.ReceivedTime.ToString('yyyy-MM-dd HH:mm:ss')
        preview = $msg.Body.Substring(0, [Math]::Min(200, $msg.Body.Length))
        unread = $msg.UnRead
        hasAttachments = ($msg.Attachments.Count -gt 0)
        attachmentCount = $msg.Attachments.Count
      }
    }
  }
  @{ success = $true; folder = $fldr.Name; emails = $emails; count = $emails.Count } | ConvertTo-Json -Depth 4 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 30000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM readEmails failed: ${err}` };
    }
  }

  // ─── Outlook COM: Compose Email (create draft) ───────────

  private async comComposeEmail(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'outlook') {
      return { success: false, error: `composeEmail is only supported for Outlook (current: ${this.officeType})` };
    }
    if (!params?.to) return { success: false, error: 'No recipient (to) provided' };
    try {
      const to = (params.to || '').replace(/'/g, "''");
      const subject = (params.subject || '').replace(/'/g, "''");
      const body = (params.body || '').replace(/'/g, "''");
      const cc = (params.cc || '').replace(/'/g, "''");
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $ol = [Runtime.Interopservices.Marshal]::GetActiveObject('Outlook.Application')
  $mail = $ol.CreateItem(0) # olMailItem
  $mail.To = '${to}'
  $mail.Subject = '${subject}'
  $mail.Body = '${body}'
  if ('${cc}') { $mail.CC = '${cc}' }
  $mail.Save()
  $mail.Display()
  @{ success = $true; action = 'draft_created'; to = '${to}'; subject = '${subject}' } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM composeEmail failed: ${err}` };
    }
  }

  // ─── Outlook COM: Send Email ─────────────────────────────

  private async comSendEmail(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'outlook') {
      return { success: false, error: `sendEmail is only supported for Outlook (current: ${this.officeType})` };
    }
    if (!params?.to) return { success: false, error: 'No recipient (to) provided' };
    try {
      const to = (params.to || '').replace(/'/g, "''");
      const subject = (params.subject || '').replace(/'/g, "''");
      const body = (params.body || '').replace(/'/g, "''");
      const cc = (params.cc || '').replace(/'/g, "''");
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $ol = [Runtime.Interopservices.Marshal]::GetActiveObject('Outlook.Application')
  $mail = $ol.CreateItem(0) # olMailItem
  $mail.To = '${to}'
  $mail.Subject = '${subject}'
  $mail.Body = '${body}'
  if ('${cc}') { $mail.CC = '${cc}' }
  $mail.Send()
  @{ success = $true; action = 'sent'; to = '${to}'; subject = '${subject}' } | ConvertTo-Json -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM sendEmail failed: ${err}` };
    }
  }

  // ─── PowerPoint COM: Read Slides ─────────────────────────

  private async comReadSlides(): Promise<ActionResult> {
    if (this.officeType !== 'powerpoint') {
      return { success: false, error: `readSlides is only supported for PowerPoint (current: ${this.officeType})` };
    }
    try {
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $ppt = [Runtime.Interopservices.Marshal]::GetActiveObject('PowerPoint.Application')
  $pres = $ppt.ActivePresentation
  if (-not $pres) { throw 'No active presentation' }
  $slides = @()
  foreach ($slide in $pres.Slides) {
    $shapes = @()
    foreach ($shape in $slide.Shapes) {
      $shapeInfo = @{
        name = $shape.Name
        type = $shape.Type
        hasText = $shape.HasTextFrame
        left = $shape.Left
        top = $shape.Top
        width = $shape.Width
        height = $shape.Height
      }
      if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
        $shapeInfo.text = $shape.TextFrame.TextRange.Text
      }
      $shapes += $shapeInfo
    }
    $slides += @{
      index = $slide.SlideIndex
      layout = $slide.Layout
      name = $slide.Name
      shapeCount = $slide.Shapes.Count
      shapes = $shapes
    }
  }
  @{ success = $true; title = $pres.Name; slideCount = $pres.Slides.Count; slides = $slides } | ConvertTo-Json -Depth 5 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 30000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM readSlides failed: ${err}` };
    }
  }

  // ─── PowerPoint COM: Read Slide Text ─────────────────────

  private async comReadSlideText(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'powerpoint') {
      return { success: false, error: `readSlideText is only supported for PowerPoint (current: ${this.officeType})` };
    }
    const slideIndex = params?.slideIndex || 1;
    try {
      const script = `
$ErrorActionPreference = 'Stop'
try {
  $ppt = [Runtime.Interopservices.Marshal]::GetActiveObject('PowerPoint.Application')
  $pres = $ppt.ActivePresentation
  if (-not $pres) { throw 'No active presentation' }
  if (${slideIndex} -gt $pres.Slides.Count) { throw 'Slide index out of range' }
  $slide = $pres.Slides.Item(${slideIndex})
  $texts = @()
  foreach ($shape in $slide.Shapes) {
    if ($shape.HasTextFrame -and $shape.TextFrame.HasText) {
      $texts += @{
        shapeName = $shape.Name
        text = $shape.TextFrame.TextRange.Text
        fontName = $shape.TextFrame.TextRange.Font.Name
        fontSize = $shape.TextFrame.TextRange.Font.Size
      }
    }
  }
  $notes = ''
  try { $notes = $slide.NotesPage.Shapes.Item(2).TextFrame.TextRange.Text } catch { }
  @{ success = $true; slideIndex = ${slideIndex}; texts = $texts; notes = $notes } | ConvertTo-Json -Depth 4 -Compress
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
      const result = runPSJsonInteractive(script, 15000) as Record<string, unknown>;
      return { success: result.success as boolean, result, error: result.error as string | undefined };
    } catch (err) {
      return { success: false, error: `COM readSlideText failed: ${err}` };
    }
  }

  // ─── Excel Cell Writing ───────────────────────────────────

  private async writeExcelCell(params?: ActionParams): Promise<ActionResult> {
    if (this.officeType !== 'excel') {
      return { success: false, error: `writeCell is only supported for Excel (current: ${this.officeType})` };
    }

    if (!params?.text && !params?.value) {
      return { success: false, error: 'No text/value provided for writeCell' };
    }

    const row = params.row || 1;
    const col = params.col || 1;
    const text = params.text || params.value || '';

    try {
      const escapedText = text.replace(/'/g, "''");
      const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$ErrorActionPreference = 'SilentlyContinue'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${this.app.pid}
)
$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$written = $false
foreach ($win in $appWindows) {
  $gridCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::DataGrid
  )
  $grids = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $gridCond)
  foreach ($grid in $grids) {
    try {
      $gridPattern = $grid.GetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern)
      if ($gridPattern) {
        $item = $gridPattern.GetItem(${row}, ${col})
        if ($item) {
          try {
            $vp = $item.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            if ($vp) {
              $vp.SetValue('${escapedText}')
              $written = $true
            }
          } catch {
            # Fallback: click and type
            $item.SetFocus()
            Start-Sleep -Milliseconds 100
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.SendKeys]::SendWait('{F2}')
            Start-Sleep -Milliseconds 50
            [System.Windows.Forms.SendKeys]::SendWait('^a')
            Start-Sleep -Milliseconds 50
            [System.Windows.Forms.SendKeys]::SendWait('${escapedText}')
            [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
            $written = $true
          }
        }
        break
      }
    } catch { }
  }
  if ($written) { break }
}

@{ success = $written; row = ${row}; col = ${col} } | ConvertTo-Json -Compress
`;

      const result = runPSJsonInteractive(script, 15000) as { success: boolean; row: number; col: number };
      return { success: result.success, result: { row: result.row, col: result.col } };
    } catch (err) {
      return { success: false, error: `Failed to write cell: ${err}` };
    }
  }
}

export default OfficePlugin;
