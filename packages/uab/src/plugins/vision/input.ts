/**
 * Vision Input Injection — Coordinate-based mouse & keyboard via Win32 API
 *
 * Provides low-level input injection for the Vision fallback:
 *   - Mouse: click, double-click, right-click, hover at (x, y)
 *   - Keyboard: keypress, hotkey combos, text typing
 *   - Window: foreground management
 *
 * Uses PowerShell → C# P/Invoke to call user32.dll directly.
 * This works with ANY window regardless of framework or accessibility support.
 */

import { runPSJsonInteractive, runPSRawInteractive } from '../../ps-exec.js';
import type { ActionResult } from '../../types.js';

// ─── Mouse Actions ───────────────────────────────────────────

/**
 * Bring a window to foreground by PID before sending input.
 */
function foregroundScript(pid: number): string {
  return `
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class VisionInput {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte sc, uint f, IntPtr e);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    public static IntPtr FindByPid(int pid) {
      IntPtr found = IntPtr.Zero;
      EnumWindows((hWnd, _) => {
        uint wpid;
        GetWindowThreadProcessId(hWnd, out wpid);
        if ((int)wpid == pid) { found = hWnd; return false; }
        return true;
      }, IntPtr.Zero);
      return found;
    }

    public static bool ForceForeground(IntPtr target) {
      IntPtr fg = GetForegroundWindow();
      if (fg == target) return true;
      uint fgPid; uint fgT = GetWindowThreadProcessId(fg, out fgPid);
      uint curT = GetCurrentThreadId();
      // Alt key trick to allow SetForegroundWindow from background
      keybd_event(0x12, 0, 0, IntPtr.Zero);
      keybd_event(0x12, 0, 0x02, IntPtr.Zero);
      if (fgT != curT) AttachThreadInput(curT, fgT, true);
      // Only restore if minimized — otherwise leave size/position intact
      // This preserves Windows 11 snap layouts and split-screen arrangements
      if (IsIconic(target)) {
        ShowWindow(target, 9); // SW_RESTORE only for minimized windows
      }
      SetForegroundWindow(target);
      BringWindowToTop(target);
      if (fgT != curT) AttachThreadInput(curT, fgT, false);
      System.Threading.Thread.Sleep(100);
      return true;
    }

    public static void LeftClick(int x, int y) {
      SetCursorPos(x, y); System.Threading.Thread.Sleep(50);
      mouse_event(0x02, 0, 0, 0, IntPtr.Zero);
      mouse_event(0x04, 0, 0, 0, IntPtr.Zero);
    }

    public static void RightClick(int x, int y) {
      SetCursorPos(x, y); System.Threading.Thread.Sleep(50);
      mouse_event(0x08, 0, 0, 0, IntPtr.Zero);
      mouse_event(0x10, 0, 0, 0, IntPtr.Zero);
    }

    public static void DoubleClick(int x, int y) {
      SetCursorPos(x, y); System.Threading.Thread.Sleep(50);
      mouse_event(0x02, 0, 0, 0, IntPtr.Zero);
      mouse_event(0x04, 0, 0, 0, IntPtr.Zero);
      System.Threading.Thread.Sleep(50);
      mouse_event(0x02, 0, 0, 0, IntPtr.Zero);
      mouse_event(0x04, 0, 0, 0, IntPtr.Zero);
    }

    public static void MoveTo(int x, int y) {
      SetCursorPos(x, y);
      mouse_event(0x01, 0, 0, 0, IntPtr.Zero);
    }
  }
'

$hWnd = [VisionInput]::FindByPid(${pid})
if ($hWnd -ne [IntPtr]::Zero) {
  [VisionInput]::ForceForeground($hWnd) | Out-Null
}
`;
}

/**
 * Click at absolute screen coordinates.
 */
export function clickAt(pid: number, x: number, y: number): ActionResult {
  const script = `${foregroundScript(pid)}
[VisionInput]::LeftClick(${Math.round(x)}, ${Math.round(y)})
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Double-click at absolute screen coordinates.
 */
export function doubleClickAt(pid: number, x: number, y: number): ActionResult {
  const script = `${foregroundScript(pid)}
[VisionInput]::DoubleClick(${Math.round(x)}, ${Math.round(y)})
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Right-click at absolute screen coordinates.
 */
export function rightClickAt(pid: number, x: number, y: number): ActionResult {
  const script = `${foregroundScript(pid)}
[VisionInput]::RightClick(${Math.round(x)}, ${Math.round(y)})
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Hover (move cursor) to absolute screen coordinates.
 */
export function hoverAt(pid: number, x: number, y: number): ActionResult {
  const script = `${foregroundScript(pid)}
[VisionInput]::MoveTo(${Math.round(x)}, ${Math.round(y)})
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Drag along a path of coordinates — P6 OS raw input injection.
 * Moves to start, holds button, traverses waypoints, releases.
 * button: 'left' (default), 'middle', 'right'
 * stepDelay controls speed in ms between waypoints (default 10ms).
 */
export function dragPath(
  pid: number,
  path: Array<{ x: number; y: number }>,
  stepDelay: number = 10,
  button: 'left' | 'middle' | 'right' = 'left',
): ActionResult {
  if (!path || path.length < 2) {
    return { success: false, error: 'Drag path requires at least 2 points (start + end)' };
  }

  // mouse_event flags: left=0x02/0x04, right=0x08/0x10, middle=0x20/0x40
  const buttonDown = button === 'middle' ? '0x20' : button === 'right' ? '0x08' : '0x02';
  const buttonUp = button === 'middle' ? '0x40' : button === 'right' ? '0x10' : '0x04';

  const script = `${foregroundScript(pid)}
# Move to start position
[VisionInput]::SetCursorPos(${Math.round(path[0].x)}, ${Math.round(path[0].y)})
[System.Threading.Thread]::Sleep(50)

# Button down
[VisionInput]::mouse_event(${buttonDown}, 0, 0, 0, [IntPtr]::Zero)
[System.Threading.Thread]::Sleep(30)

# Traverse waypoints
$points = @(
  ${path.slice(1).map(p => `@(${Math.round(p.x)}, ${Math.round(p.y)})`).join(',\n  ')}
)
foreach ($pt in $points) {
  [VisionInput]::SetCursorPos($pt[0], $pt[1])
  [VisionInput]::mouse_event(0x01, 0, 0, 0, [IntPtr]::Zero)
  [System.Threading.Thread]::Sleep(${stepDelay})
}

# Button up
[VisionInput]::mouse_event(${buttonUp}, 0, 0, 0, [IntPtr]::Zero)

@{ success = $true; points = ${path.length}; button = '${button}' } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 30000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Scroll at absolute coordinates using mouse wheel injection.
 * amount > 0 scrolls up, amount < 0 scrolls down. Each unit = 120 (one notch).
 */
export function scrollAt(
  pid: number,
  x: number,
  y: number,
  amount: number,
): ActionResult {
  const wheelDelta = amount * 120;
  const script = `${foregroundScript(pid)}
[VisionInput]::SetCursorPos(${Math.round(x)}, ${Math.round(y)})
[System.Threading.Thread]::Sleep(50)
# mouse_event MOUSEEVENTF_WHEEL = 0x0800
[VisionInput]::mouse_event(0x0800, 0, 0, ${wheelDelta}, [IntPtr]::Zero)
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Keyboard Actions ────────────────────────────────────────

// SendKeys format mapping
const SENDKEYS_MAP: Record<string, string> = {
  backspace: '{BACKSPACE}', tab: '{TAB}', enter: '{ENTER}', return: '{ENTER}',
  escape: '{ESC}', esc: '{ESC}', space: ' ',
  pageup: '{PGUP}', pagedown: '{PGDN}',
  end: '{END}', home: '{HOME}',
  left: '{LEFT}', up: '{UP}', right: '{RIGHT}', down: '{DOWN}',
  insert: '{INSERT}', delete: '{DELETE}',
  f1: '{F1}', f2: '{F2}', f3: '{F3}', f4: '{F4}',
  f5: '{F5}', f6: '{F6}', f7: '{F7}', f8: '{F8}',
  f9: '{F9}', f10: '{F10}', f11: '{F11}', f12: '{F12}',
  '+': '{+}', '^': '{^}', '%': '{%}', '~': '{~}',
};

/**
 * Send a single keypress to the foreground window.
 */
export function sendKeypress(pid: number, key: string): ActionResult {
  const mapped = SENDKEYS_MAP[key.toLowerCase()] || key;
  const escaped = mapped.replace(/'/g, "''");
  const script = `${foregroundScript(pid)}
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Send a hotkey combination (e.g., ['ctrl', 's']).
 */
export function sendHotkey(pid: number, keys: string[]): ActionResult {
  // Build SendKeys combo: ctrl=^, shift=+, alt=%
  let combo = '';
  const modifiers: string[] = [];
  let mainKey = '';

  for (const k of keys) {
    const lower = k.toLowerCase();
    if (lower === 'ctrl' || lower === 'control') modifiers.push('^');
    else if (lower === 'shift') modifiers.push('+');
    else if (lower === 'alt') modifiers.push('%');
    else mainKey = SENDKEYS_MAP[lower] || k;
  }

  combo = modifiers.join('') + mainKey;
  const escaped = combo.replace(/'/g, "''");

  const script = `${foregroundScript(pid)}
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Type text into the currently focused element.
 * Clicks at coordinates first to ensure focus, then types.
 */
export function typeTextAt(pid: number, x: number, y: number, text: string): ActionResult {
  const escaped = text.replace(/'/g, "''")
    .replace(/\+/g, '{+}').replace(/\^/g, '{^}')
    .replace(/%/g, '{%}').replace(/~/g, '{~}')
    .replace(/\(/g, '{(}').replace(/\)/g, '{)}')
    .replace(/\{/g, '{{}').replace(/\}/g, '{}}');

  const script = `${foregroundScript(pid)}
Add-Type -AssemblyName System.Windows.Forms
[VisionInput]::LeftClick(${Math.round(x)}, ${Math.round(y)})
Start-Sleep -Milliseconds 200
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 15000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Type a full string into the focused window in one shot.
 * Brings the window to foreground first, then sends all text at once.
 * Much faster than per-character keypress — one call for any length string.
 */
export function typeText(pid: number, text: string): ActionResult {
  const escaped = text.replace(/'/g, "''")
    .replace(/\+/g, '{+}').replace(/\^/g, '{^}')
    .replace(/%/g, '{%}').replace(/~/g, '{~}')
    .replace(/\(/g, '{(}').replace(/\)/g, '{)}')
    .replace(/\{/g, '{{}').replace(/\}/g, '{}}');

  const script = `${foregroundScript(pid)}
Add-Type -AssemblyName System.Windows.Forms
Start-Sleep -Milliseconds 100
[System.Windows.Forms.SendKeys]::SendWait('${escaped}')
@{ success = $true } | ConvertTo-Json -Compress
`;
  try {
    return runPSJsonInteractive(script, 15000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Window Actions ──────────────────────────────────────────

/**
 * Window management via Win32 API.
 */
export function windowAction(
  pid: number,
  action: 'minimize' | 'maximize' | 'restore' | 'close',
): ActionResult {
  const cmdMap: Record<string, string> = {
    minimize: 'ShowWindow($hWnd, 6)',
    maximize: 'ShowWindow($hWnd, 3)',
    restore: 'ShowWindow($hWnd, 9)',
    close: 'PostMessage($hWnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)',
  };

  const cmd = cmdMap[action];
  if (!cmd) return { success: false, error: `Unknown window action: ${action}` };

  const script = `
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class VWin {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    public static IntPtr FindByPid(int pid) {
      IntPtr found = IntPtr.Zero;
      EnumWindows((hWnd, _) => {
        uint wpid; GetWindowThreadProcessId(hWnd, out wpid);
        if ((int)wpid == pid) { found = hWnd; return false; }
        return true;
      }, IntPtr.Zero);
      return found;
    }
  }
'
$hWnd = [VWin]::FindByPid(${pid})
if ($hWnd -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'Window not found' } | ConvertTo-Json -Compress
} else {
  [VWin]::${cmd} | Out-Null
  @{ success = $true } | ConvertTo-Json -Compress
}
`;
  try {
    return runPSJsonInteractive(script, 10000) as ActionResult;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Screenshot Capture ──────────────────────────────────────

/**
 * Capture a screenshot of a window by PID.
 * Returns the file path and base64-encoded image data.
 */
export function captureScreenshot(
  pid: number,
  outputPath: string,
): { success: boolean; path?: string; base64?: string; width?: number; height?: number; error?: string } {
  const escapedPath = outputPath.replace(/\\/g, '\\\\').replace(/'/g, "''");

  const script = `
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$sdAssembly = [System.Drawing.Bitmap].Assembly.Location
Add-Type -ReferencedAssemblies $sdAssembly -TypeDefinition '
  using System;
  using System.Drawing;
  using System.Drawing.Imaging;
  using System.Runtime.InteropServices;
  using System.IO;
  public class VisionCapture {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, int nFlags);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hWnd);
    [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public static string CaptureToFile(IntPtr hWnd, string path) {
      // Enable per-monitor DPI awareness for accurate coordinates
      try { SetProcessDpiAwareness(2); } catch { try { SetProcessDPIAware(); } catch {} }

      RECT rect;
      if (!GetWindowRect(hWnd, out rect)) return "ERR:GetWindowRect failed";
      int w = rect.Right - rect.Left;
      int h = rect.Bottom - rect.Top;
      if (w <= 0 || h <= 0) return "ERR:Zero size " + w + "x" + h;

      // Get DPI scaling for hi-res capture
      uint dpi = 96;
      try { dpi = GetDpiForWindow(hWnd); } catch {}
      float scale = dpi / 96f;
      int captureW = (int)(w * scale);
      int captureH = (int)(h * scale);

      using (Bitmap bmp = new Bitmap(captureW, captureH)) {
        bmp.SetResolution(dpi, dpi);
        using (Graphics g = Graphics.FromImage(bmp)) {
          g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
          IntPtr hdc = g.GetHdc();
          bool ok = PrintWindow(hWnd, hdc, 2);
          g.ReleaseHdc(hdc);
          if (!ok) {
            // Fallback: screen capture at physical resolution
            g.CopyFromScreen(rect.Left, rect.Top, 0, 0, new Size(captureW, captureH));
          }
        }
        string dir = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
          Directory.CreateDirectory(dir);
        bmp.Save(path, ImageFormat.Png);
      }
      return "OK:" + rect.Left + "," + rect.Top + "," + captureW + "," + captureH;
    }

    public static string ToBase64(string path) {
      byte[] bytes = File.ReadAllBytes(path);
      return Convert.ToBase64String(bytes);
    }
  }
'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$windows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$bestWindow = $null
$bestArea = 0
foreach ($w in $windows) {
  $rect = $w.Current.BoundingRectangle
  $cls = $w.Current.ClassName
  $name = $w.Current.Name
  if (-not $rect.IsEmpty -and $rect.Width -gt 50 -and $rect.Height -gt 50) {
    if ($cls -eq 'Progman' -or $cls -eq 'Shell_TrayWnd' -or $cls -eq 'Shell_SecondaryTrayWnd') { continue }
    if ($name -eq 'Program Manager') { continue }
    if ($rect.Width / $rect.Height -gt 8) { continue }
    $area = $rect.Width * $rect.Height
    if ($area -gt $bestArea) { $bestArea = $area; $bestWindow = $w }
  }
}

if (-not $bestWindow) {
  @{ success = $false; error = 'No suitable window found' } | ConvertTo-Json -Compress
  exit
}

$nativeHandle = [IntPtr]$bestWindow.Current.NativeWindowHandle
if ($nativeHandle -eq [IntPtr]::Zero) {
  @{ success = $false; error = 'No native window handle' } | ConvertTo-Json -Compress
  exit
}

$result = [VisionCapture]::CaptureToFile($nativeHandle, '${escapedPath}')
if ($result.StartsWith('OK:')) {
  $dims = $result.Substring(3).Split(',')
  $b64 = [VisionCapture]::ToBase64('${escapedPath}')
  @{
    success = $true
    path = '${escapedPath}'
    base64 = $b64
    winX = [int]$dims[0]
    winY = [int]$dims[1]
    width = [int]$dims[2]
    height = [int]$dims[3]
  } | ConvertTo-Json -Compress
} else {
  @{ success = $false; error = $result } | ConvertTo-Json -Compress
}
`;

  try {
    const raw = runPSJsonInteractive(script, 20000) as {
      success: boolean;
      path?: string;
      base64?: string;
      winX?: number;
      winY?: number;
      width?: number;
      height?: number;
      error?: string;
    };

    if (raw.success) {
      return {
        success: true,
        path: raw.path,
        base64: raw.base64,
        width: raw.width,
        height: raw.height,
      };
    }
    return { success: false, error: raw.error };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Get window bounds (position + size) for a PID.
 */
export function getWindowBounds(pid: number): {
  success: boolean;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  title?: string;
  error?: string;
} {
  const script = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$windows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$bestWindow = $null
$bestArea = 0
foreach ($w in $windows) {
  $rect = $w.Current.BoundingRectangle
  $cls = $w.Current.ClassName
  $name = $w.Current.Name
  if (-not $rect.IsEmpty -and $rect.Width -gt 50 -and $rect.Height -gt 50) {
    if ($cls -eq 'Progman' -or $cls -eq 'Shell_TrayWnd') { continue }
    if ($name -eq 'Program Manager') { continue }
    if ($rect.Width / $rect.Height -gt 8) { continue }
    $area = $rect.Width * $rect.Height
    if ($area -gt $bestArea) { $bestArea = $area; $bestWindow = $w }
  }
}

if ($bestWindow) {
  $rect = $bestWindow.Current.BoundingRectangle
  @{
    success = $true
    x = [math]::Round($rect.X)
    y = [math]::Round($rect.Y)
    width = [math]::Round($rect.Width)
    height = [math]::Round($rect.Height)
    title = $bestWindow.Current.Name
  } | ConvertTo-Json -Compress
} else {
  @{ success = $false; error = 'No window found' } | ConvertTo-Json -Compress
}
`;

  try {
    return runPSJsonInteractive(script, 10000) as any;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}
