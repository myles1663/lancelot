/**
 * Framework Detector
 *
 * Identifies which UI framework a running application uses by inspecting
 * loaded DLLs, process signatures, and binary characteristics.
 * Windows-focused implementation with extensible platform support.
 *
 * Phase 3 Enhancement: Full DLL module scanning in detectAll() for
 * accurate framework detection across all running processes.
 */

import * as path from 'path';
import * as fs from 'fs';
import type { DetectedApp, FrameworkType } from './types.js';
import { runPSJson, runPSRaw, runPSRawInteractive } from './ps-exec.js';

interface ProcessInfo {
  pid: number;
  name: string;
  path: string;
  commandLine: string;
  modules: string[];
  windowTitle: string;
}

interface FrameworkSignature {
  framework: FrameworkType;
  modules: string[];
  commandLine: string[];
  filePatterns: string[];
  baseConfidence: number;
}

// ─── System processes to skip ──────────────────────────────────

const SYSTEM_PROCESSES = new Set([
  'system', 'svchost.exe', 'csrss.exe', 'lsass.exe', 'services.exe',
  'smss.exe', 'wininit.exe', 'winlogon.exe', 'dwm.exe', 'conhost.exe',
  'audiodg.exe', 'taskhostw.exe', 'runtimebroker.exe', 'searchhost.exe',
  'ctfmon.exe', 'fontdrvhost.exe', 'lsaiso.exe', 'securityhealthservice.exe',
  'sgrmbroker.exe', 'spoolsv.exe', 'dashost.exe', 'dllhost.exe',
  'sihost.exe', 'startmenuexperiencehost.exe', 'textinputhost.exe',
  'widgetservice.exe', 'shellexperiencehost.exe', 'applicationframehost.exe',
  'systemsettings.exe', 'lockapp.exe', 'searchapp.exe',
]);

// ─── Framework signatures ──────────────────────────────────────

const SIGNATURES: FrameworkSignature[] = [
  {
    framework: 'electron',
    modules: ['electron.exe', 'libcef.dll', 'chrome_elf.dll', 'v8.dll', 'electron.dll'],
    commandLine: ['--type=renderer', 'electron', 'app.asar', '--remote-debugging-port'],
    filePatterns: ['resources/app.asar', 'resources/app', 'electron.exe'],
    baseConfidence: 0.9,
  },
  {
    framework: 'qt6',
    modules: ['qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll', 'qt6quick.dll', 'qt6qml.dll'],
    commandLine: [],
    filePatterns: ['Qt6Core.dll', 'Qt6Gui.dll'],
    baseConfidence: 0.85,
  },
  {
    framework: 'qt5',
    modules: ['qt5core.dll', 'qt5gui.dll', 'qt5widgets.dll', 'qt5quick.dll', 'qt5qml.dll'],
    commandLine: [],
    filePatterns: ['Qt5Core.dll', 'Qt5Gui.dll'],
    baseConfidence: 0.85,
  },
  {
    framework: 'gtk4',
    modules: ['libgtk-4-1.dll', 'libgtk-4.dll', 'gtk-4.dll'],
    commandLine: [],
    filePatterns: [],
    baseConfidence: 0.85,
  },
  {
    framework: 'gtk3',
    modules: ['libgtk-3-0.dll', 'libgtk-3.dll', 'gtk-3.dll'],
    commandLine: [],
    filePatterns: [],
    baseConfidence: 0.85,
  },
  {
    framework: 'wpf',
    modules: ['wpfgfx_cor3.dll', 'wpfgfx_v0400.dll', 'presentationframework.dll', 'presentationcore.dll'],
    commandLine: [],
    filePatterns: [],
    baseConfidence: 0.85,
  },
  {
    framework: 'dotnet',
    modules: ['coreclr.dll', 'clrjit.dll', 'mscorlib.dll', 'system.windows.forms.dll'],
    commandLine: ['dotnet'],
    filePatterns: [],
    baseConfidence: 0.7,
  },
  {
    framework: 'flutter',
    modules: ['flutter_windows.dll', 'flutter_engine.dll', 'dart.dll'],
    commandLine: ['flutter', 'dart'],
    filePatterns: ['flutter_windows.dll'],
    baseConfidence: 0.85,
  },
  {
    framework: 'java-swing',
    modules: ['jvm.dll', 'java.dll', 'jawt.dll'],
    commandLine: ['java', 'javaw', '-jar'],
    filePatterns: [],
    baseConfidence: 0.7,
  },
  {
    framework: 'office',
    // Only highly specific Office DLLs — no substrings that match Windows system DLLs
    // Avoided: ppcore.dll (matches kernel.appcore.dll), riched20.dll (Notepad loads it)
    modules: ['wwlib.dll', 'xlcall32.dll', 'olmapi32.dll', 'mso40uiwin32client.dll', 'mso30win32client.dll'],
    commandLine: [],  // Don't use cmdline — words like 'excel'/'outlook' appear in many paths
    filePatterns: [],
    baseConfidence: 0.9,
  },
];

// ─── Process discovery ─────────────────────────────────────────

function getRunningProcesses(): ProcessInfo[] {
  try {
    const script = `Get-CimInstance Win32_Process | Select-Object ProcessId,Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress`;
    const rawList = runPSJson(script, 15000) as Array<Record<string, unknown>>;
    const processes: ProcessInfo[] = [];

    for (const item of rawList) {
      const pid = item.ProcessId as number;
      const name = (item.Name as string) || '';
      const exePath = (item.ExecutablePath as string) || '';
      const commandLine = (item.CommandLine as string) || '';
      if (!pid || !name) continue;

      processes.push({
        pid,
        name,
        path: exePath,
        commandLine,
        modules: [],
        windowTitle: '',
      });
    }
    return processes;
  } catch {
    return [];
  }
}

/**
 * Batch-scan loaded DLLs for multiple processes in a single PowerShell call.
 * This is dramatically faster than calling Get-Process per PID individually.
 * Returns a Map of PID → lowercased module names.
 */
function batchGetProcessModules(pids: number[]): Map<number, string[]> {
  const result = new Map<number, string[]>();
  if (pids.length === 0) return result;

  // Process in batches to avoid PS command limits
  const BATCH_SIZE = 50;
  for (let i = 0; i < pids.length; i += BATCH_SIZE) {
    const batch = pids.slice(i, i + BATCH_SIZE);
    try {
      // Don't filter on MainWindowHandle — it's always 0 in non-interactive sessions.
      // Instead, just try all candidate PIDs (already filtered by SYSTEM_PROCESSES).
      const psScript = `
$procs = Get-Process -Id @(${batch.join(',')}) -ErrorAction SilentlyContinue
$output = @()
foreach ($p in $procs) {
  try {
    $mods = ($p.Modules | Select-Object -ExpandProperty ModuleName) -join ','
    if ($mods) { $output += "$($p.Id)|$mods" }
  } catch { }
}
$output -join ';'
`;

      const output = runPSRaw(psScript, 30000);

      for (const entry of output.trim().split(';')) {
        if (!entry) continue;
        const barIdx = entry.indexOf('|');
        if (barIdx === -1) continue;
        const pid = parseInt(entry.substring(0, barIdx), 10);
        const modules = entry.substring(barIdx + 1)
          .split(',')
          .map(m => m.trim().toLowerCase())
          .filter(Boolean);
        if (!isNaN(pid) && modules.length > 0) {
          result.set(pid, modules);
        }
      }
    } catch { /* batch scan failed — fall back to per-process detection */ }
  }

  return result;
}

function getProcessModules(pid: number): string[] {
  try {
    const script = `(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).Modules | Select-Object -ExpandProperty ModuleName`;
    const output = runPSRaw(script, 5000);
    return output.trim().split('\n').map(m => m.trim().toLowerCase()).filter(Boolean);
  } catch {
    return [];
  }
}

/**
 * Batch-fetch window titles for multiple PIDs in one PowerShell call.
 */
function batchGetWindowTitles(pids: number[]): Map<number, string> {
  const result = new Map<number, string>();
  if (pids.length === 0) return result;

  try {
    // Use Win32 EnumWindows API instead of Get-Process.MainWindowTitle
    // because MainWindowHandle is always 0 in non-interactive sessions.
    const pidSet = pids.join(',');
    const psScript = `
Add-Type -TypeDefinition '
  using System;
  using System.Text;
  using System.Collections.Generic;
  using System.Runtime.InteropServices;

  public class WinEnum {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);

    public static Dictionary<uint, string> GetWindowTitles() {
      var titles = new Dictionary<uint, string>();
      EnumWindows((hWnd, lParam) => {
        if (!IsWindowVisible(hWnd)) return true;
        int len = GetWindowTextLength(hWnd);
        if (len == 0) return true;
        uint pid;
        GetWindowThreadProcessId(hWnd, out pid);
        StringBuilder sb = new StringBuilder(len + 1);
        GetWindowText(hWnd, sb, sb.Capacity);
        string title = sb.ToString();
        if (!string.IsNullOrWhiteSpace(title) && !titles.ContainsKey(pid)) {
          titles[pid] = title;
        }
        return true;
      }, IntPtr.Zero);
      return titles;
    }
  }
'

$targetPids = @(${pidSet})
$targetSet = New-Object 'System.Collections.Generic.HashSet[uint32]'
foreach ($p in $targetPids) { [void]$targetSet.Add([uint32]$p) }
$titles = [WinEnum]::GetWindowTitles()
foreach ($kv in $titles.GetEnumerator()) {
  if ($targetSet.Contains($kv.Key)) {
    Write-Output "$($kv.Key)|$($kv.Value)"
  }
}
`;

    // Use interactive session bridge — EnumWindows only works in the desktop session
    const output = runPSRawInteractive(psScript, 15000);

    for (const line of output.trim().split('\n')) {
      if (!line) continue;
      const barIdx = line.indexOf('|');
      if (barIdx === -1) continue;
      const pid = parseInt(line.substring(0, barIdx), 10);
      const title = line.substring(barIdx + 1).trim();
      if (!isNaN(pid) && title) result.set(pid, title);
    }
  } catch { /* fallback to individual calls */ }

  return result;
}

function getWindowTitle(pid: number): string {
  try {
    // Use Win32 EnumWindows API — MainWindowTitle is always empty in non-interactive sessions
    const script = `
Add-Type -TypeDefinition '
  using System;
  using System.Text;
  using System.Runtime.InteropServices;

  public class WinTitle {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);

    public static string GetTitle(uint targetPid) {
      string result = "";
      EnumWindows((hWnd, lParam) => {
        if (!IsWindowVisible(hWnd)) return true;
        int len = GetWindowTextLength(hWnd);
        if (len == 0) return true;
        uint pid;
        GetWindowThreadProcessId(hWnd, out pid);
        if (pid == targetPid) {
          StringBuilder sb = new StringBuilder(len + 1);
          GetWindowText(hWnd, sb, sb.Capacity);
          result = sb.ToString();
          return false;
        }
        return true;
      }, IntPtr.Zero);
      return result;
    }
  }
'
Write-Output ([WinTitle]::GetTitle(${pid}))
`;
    // Use interactive session bridge — window titles only visible from desktop session
    return runPSRawInteractive(script, 10000);
  } catch {
    return '';
  }
}

function detectElectronDebugPort(proc: ProcessInfo): number | null {
  const match = proc.commandLine.match(/--remote-debugging-port=(\d+)/);
  if (match) return parseInt(match[1], 10);
  return null;
}

function findElectronApps(): DetectedApp[] {
  const apps: DetectedApp[] = [];
  const processes = getRunningProcesses();

  for (const proc of processes) {
    const nameLower = proc.name.toLowerCase();
    const cmdLower = proc.commandLine.toLowerCase();

    const isElectron =
      cmdLower.includes('electron') ||
      cmdLower.includes('app.asar') ||
      cmdLower.includes('--type=renderer') ||
      nameLower === 'electron.exe';

    let hasElectronFiles = false;
    if (proc.path) {
      const dir = path.dirname(proc.path);
      try {
        const checkFiles = ['resources/app.asar', 'resources/app.asar.unpacked'];
        for (const f of checkFiles) {
          try {
            fs.accessSync(path.join(dir, f));
            hasElectronFiles = true;
            break;
          } catch { /* not found */ }
        }
      } catch { /* skip */ }
    }

    if (isElectron || hasElectronFiles) {
      const debugPort = detectElectronDebugPort(proc);
      apps.push({
        pid: proc.pid,
        name: proc.name.replace('.exe', ''),
        path: proc.path,
        framework: 'electron',
        confidence: hasElectronFiles ? 0.95 : 0.8,
        connectionInfo: debugPort ? { debugPort, protocol: 'cdp' } : { protocol: 'cdp' },
        windowTitle: getWindowTitle(proc.pid),
      });
    }
  }
  return apps;
}

// Office executables — detected by process name for high confidence
const OFFICE_PROCESS_NAMES = new Set([
  'winword.exe', 'excel.exe', 'powerpnt.exe', 'outlook.exe',
  'onenote.exe', 'msaccess.exe', 'mspub.exe', 'visio.exe',
]);

function detectFramework(proc: ProcessInfo): { framework: FrameworkType; confidence: number } {
  const nameLower = proc.name.toLowerCase();
  const cmdLower = proc.commandLine.toLowerCase();

  // Fast path: Office apps detected by executable name
  if (OFFICE_PROCESS_NAMES.has(nameLower)) {
    return { framework: 'office', confidence: 0.95 };
  }

  for (const sig of SIGNATURES) {
    let score = 0;
    let matches = 0;

    for (const pattern of sig.commandLine) {
      if (cmdLower.includes(pattern.toLowerCase())) {
        score += 0.3;
        matches++;
      }
    }

    for (const mod of sig.modules) {
      if (nameLower.includes(mod.replace('.dll', '').replace('.exe', ''))) {
        score += 0.4;
        matches++;
      }
      if (proc.modules.length > 0) {
        // Exact filename match — avoid false positives from substring matching
        // e.g. 'ppcore.dll' should NOT match 'kernel.appcore.dll'
        const modLower = mod.toLowerCase();
        if (proc.modules.some(m => m === modLower || m.endsWith('\\' + modLower) || m.endsWith('/' + modLower))) {
          score += 0.5;
          matches++;
        }
      }
    }

    if (proc.path) {
      const dir = path.dirname(proc.path);
      for (const pattern of sig.filePatterns) {
        try {
          fs.accessSync(path.join(dir, pattern));
          score += 0.4;
          matches++;
        } catch { /* not found */ }
      }
    }

    if (matches > 0) {
      const confidence = Math.min(sig.baseConfidence + (score * 0.1), 1.0);
      return { framework: sig.framework, confidence };
    }
  }
  return { framework: 'unknown', confidence: 0 };
}

// ─── Framework Detector Class ──────────────────────────────────

export class FrameworkDetector {
  private cache: Map<number, DetectedApp> = new Map();

  /**
   * Detect all controllable apps with enhanced DLL module scanning.
   * Uses batch PowerShell calls for performance — scans loaded DLLs
   * and window titles for all GUI processes in one shot.
   */
  async detectAll(): Promise<DetectedApp[]> {
    const apps: DetectedApp[] = [];
    const processes = getRunningProcesses();

    // Filter to candidate processes
    const candidates = processes.filter(
      p => p.pid >= 100 && !SYSTEM_PROCESSES.has(p.name.toLowerCase())
    );
    const candidatePids = candidates.map(p => p.pid);

    // Batch-scan loaded DLLs for all GUI processes at once (single PS call)
    const moduleMap = batchGetProcessModules(candidatePids);

    // Batch-fetch window titles (single PS call)
    const titleMap = batchGetWindowTitles(candidatePids);

    for (const proc of candidates) {
      // Inject loaded modules from batch scan
      proc.modules = moduleMap.get(proc.pid) || [];
      proc.windowTitle = titleMap.get(proc.pid) || '';

      const { framework, confidence } = detectFramework(proc);

      // Include unknown-framework processes if they have a window title
      // (Win-UIA can control any Windows GUI app)
      if (framework !== 'unknown' || proc.windowTitle) {
        const app: DetectedApp = {
          pid: proc.pid,
          name: proc.name.replace('.exe', ''),
          path: proc.path,
          framework,
          confidence: framework === 'unknown' ? 0.5 : confidence,
          windowTitle: proc.windowTitle,
        };

        if (framework === 'electron') {
          const debugPort = detectElectronDebugPort(proc);
          app.connectionInfo = debugPort ? { debugPort, protocol: 'cdp' } : { protocol: 'cdp' };
        }

        apps.push(app);
        this.cache.set(proc.pid, app);
      }
    }

    const seen = new Set<number>();
    return apps.filter(app => {
      if (seen.has(app.pid)) return false;
      seen.add(app.pid);
      return true;
    });
  }

  async detectElectron(): Promise<DetectedApp[]> {
    return findElectronApps();
  }

  async detectByPid(pid: number): Promise<DetectedApp | null> {
    if (this.cache.has(pid)) return this.cache.get(pid)!;

    const processes = getRunningProcesses();
    const proc = processes.find(p => p.pid === pid);
    if (!proc) return null;

    proc.modules = getProcessModules(pid);
    const { framework, confidence } = detectFramework(proc);

    // Allow 'unknown' framework — Win-UIA can handle any Windows app
    const app: DetectedApp = {
      pid,
      name: proc.name.replace('.exe', ''),
      path: proc.path,
      framework,
      confidence: framework === 'unknown' ? 0.5 : confidence,
      windowTitle: getWindowTitle(pid),
    };

    if (framework === 'electron') {
      const debugPort = detectElectronDebugPort(proc);
      app.connectionInfo = debugPort ? { debugPort, protocol: 'cdp' } : { protocol: 'cdp' };
    }

    this.cache.set(pid, app);
    return app;
  }

  async findByName(name: string): Promise<DetectedApp[]> {
    const all = await this.detectAll();
    const nameLower = name.toLowerCase();
    return all.filter(app =>
      app.name.toLowerCase().includes(nameLower) ||
      (app.windowTitle?.toLowerCase().includes(nameLower))
    );
  }

  clearCache(): void {
    this.cache.clear();
  }
}
