/**
 * Shared PowerShell execution utilities for UAB plugins.
 *
 * Uses temp files for script execution to avoid command line length limits
 * and all escaping issues with quotes, newlines, and special chars.
 *
 * Session Bridge: When running from Session 0 (non-interactive, e.g. via
 * SSH or service), desktop window APIs (EnumWindows, UIA, etc.) fail because
 * they can't see Session 1's desktop. The interactive execution functions
 * use Windows Task Scheduler to run scripts in the user's interactive session.
 */

import { execSync } from 'child_process';
import { writeFileSync, readFileSync, unlinkSync, mkdirSync, existsSync } from 'fs';
import { join } from 'path';
import { randomUUID } from 'crypto';
import { tmpdir } from 'os';

const TEMP_DIR = join(tmpdir(), 'uab-ps');

function ensureTempDir(): void {
  mkdirSync(TEMP_DIR, { recursive: true });
}

/**
 * Write script to a temp file and execute via -File.
 * This avoids the 8191-char command line limit and all escaping issues.
 */
function execPSFile(script: string, timeoutMs: number): string {
  ensureTempDir();
  const tempFile = join(TEMP_DIR, `uab-${randomUUID().substring(0, 8)}.ps1`);

  try {
    writeFileSync(tempFile, script, 'utf-8');
    return execSync(
      `powershell -NoProfile -ExecutionPolicy Bypass -File "${tempFile}"`,
      { encoding: 'utf-8', timeout: timeoutMs, maxBuffer: 10 * 1024 * 1024 }
    );
  } finally {
    try { unlinkSync(tempFile); } catch { /* ignore cleanup errors */ }
  }
}

// ─── Session Detection ──────────────────────────────────────

let _sessionId: number | null = null;

/**
 * Check if we're running in Session 0 (non-interactive).
 * Caches the result since session ID doesn't change during execution.
 */
export function isSession0(): boolean {
  if (_sessionId === null) {
    try {
      const output = execPSFile('(Get-Process -Id $PID).SessionId', 5000);
      _sessionId = parseInt(output.trim(), 10);
    } catch {
      _sessionId = -1;
    }
  }
  return _sessionId === 0;
}

// ─── Interactive Session Execution ──────────────────────────

/**
 * Execute a PowerShell script in the interactive desktop session.
 * Uses Windows Task Scheduler with /IT flag to bridge Session 0→1.
 *
 * The script's stdout is captured via output redirection to a temp file,
 * which is then read back and returned.
 *
 * @param script - PowerShell script to execute
 * @param timeoutMs - Maximum wait time in ms
 * @returns The script's output (from the temp output file)
 */
function execPSInteractive(script: string, timeoutMs: number): string {
  ensureTempDir();
  const id = randomUUID().substring(0, 8);
  const scriptFile = join(TEMP_DIR, `uab-i-${id}.ps1`);
  const outputFile = join(TEMP_DIR, `uab-o-${id}.txt`);
  const doneFile = join(TEMP_DIR, `uab-d-${id}.txt`);
  const taskName = `UAB-${id}`;

  // Wrap the user's script to redirect output to a file and signal completion
  const wrappedScript = `
try {
  $output = & {
${script}
  } 2>&1 | Out-String
  [System.IO.File]::WriteAllText('${outputFile.replace(/\\/g, '\\\\')}', $output, [System.Text.Encoding]::UTF8)
} catch {
  [System.IO.File]::WriteAllText('${outputFile.replace(/\\/g, '\\\\')}', "ERROR: $($_.Exception.Message)", [System.Text.Encoding]::UTF8)
} finally {
  [System.IO.File]::WriteAllText('${doneFile.replace(/\\/g, '\\\\')}', 'done', [System.Text.Encoding]::UTF8)
}
`;

  try {
    writeFileSync(scriptFile, wrappedScript, 'utf-8');

    // Create and run scheduled task in interactive session
    const psPath = 'powershell.exe';
    const args = `-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "${scriptFile}"`;
    execSync(
      `schtasks /Create /TN "${taskName}" /SC ONCE /ST 00:00 /IT /F /TR "${psPath} ${args}" 2>nul`,
      { encoding: 'utf-8', timeout: 5000 }
    );
    execSync(
      `schtasks /Run /TN "${taskName}" 2>nul`,
      { encoding: 'utf-8', timeout: 5000 }
    );

    // Poll for completion
    const startTime = Date.now();
    const pollInterval = 100;
    while (Date.now() - startTime < timeoutMs) {
      if (existsSync(doneFile)) {
        break;
      }
      execSync(`powershell -NoProfile -Command "Start-Sleep -Milliseconds ${pollInterval}"`, {
        timeout: pollInterval + 1000,
      });
    }

    // Read output
    if (existsSync(outputFile)) {
      return readFileSync(outputFile, 'utf-8');
    }
    return '';
  } finally {
    // Cleanup
    try { execSync(`schtasks /Delete /TN "${taskName}" /F 2>nul`, { timeout: 3000 }); } catch { /* ignore */ }
    try { unlinkSync(scriptFile); } catch { /* ignore */ }
    try { unlinkSync(outputFile); } catch { /* ignore */ }
    try { unlinkSync(doneFile); } catch { /* ignore */ }
  }
}

// ─── Public API ─────────────────────────────────────────────

/**
 * Execute a PowerShell script and parse the JSON output.
 * Handles PowerShell Infinity/-Infinity values (not valid JSON) by replacing with 0.
 */
export function runPSJson(script: string, timeoutMs: number = 15000): unknown {
  const output = execPSFile(script, timeoutMs);
  // PowerShell's ConvertTo-Json outputs Infinity/-Infinity for infinite doubles
  // (e.g., minimized window bounds). Replace with 0 for valid JSON.
  const sanitized = output.trim()
    .replace(/:\s*-Infinity/g, ': 0')
    .replace(/:\s*Infinity/g, ': 0')
    .replace(/:\s*NaN/g, ': 0');
  return JSON.parse(sanitized);
}

/**
 * Execute a PowerShell script and return raw stdout text.
 */
export function runPSRaw(script: string, timeoutMs: number = 15000): string {
  return execPSFile(script, timeoutMs).trim();
}

/**
 * Execute a PowerShell script in the interactive desktop session and parse JSON output.
 * Uses the session bridge (schtasks) when in Session 0.
 * Falls back to direct execution when already in an interactive session.
 */
export function runPSJsonInteractive(script: string, timeoutMs: number = 15000): unknown {
  const output = isSession0()
    ? execPSInteractive(script, timeoutMs)
    : execPSFile(script, timeoutMs);
  const sanitized = output.trim()
    .replace(/:\s*-Infinity/g, ': 0')
    .replace(/:\s*Infinity/g, ': 0')
    .replace(/:\s*NaN/g, ': 0');
  return JSON.parse(sanitized);
}

/**
 * Execute a PowerShell script in the interactive desktop session and return raw text.
 * Uses the session bridge (schtasks) when in Session 0.
 * Falls back to direct execution when already in an interactive session.
 */
export function runPSRawInteractive(script: string, timeoutMs: number = 15000): string {
  if (isSession0()) {
    return execPSInteractive(script, timeoutMs).trim();
  }
  return execPSFile(script, timeoutMs).trim();
}
