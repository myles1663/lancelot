/**
 * Windows UI Automation Plugin — Phase 3 Enhanced
 *
 * Controls desktop apps via the Windows UI Automation API (UIA).
 * This is the "accessibility" fallback in the control router —
 * it works with virtually any Windows GUI app: WPF, WinForms,
 * Qt, GTK, Java Swing, native Win32, and more.
 *
 * Phase 3 Enhancements:
 *   - Keyboard input (keypress, hotkey combos)
 *   - Window management (minimize, maximize, restore, move, resize, close)
 *   - Screenshot capture (per-window via Win32 API)
 *   - Deep WPF patterns (TextPattern, GridPattern, ScrollItemPattern)
 *
 * Implementation: Spawns PowerShell processes that use the
 * System.Windows.Automation .NET namespace and Win32 APIs.
 */

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
  ElementType,
} from '../../types.js';
import { runPSJsonInteractive, runPSRawInteractive } from '../../ps-exec.js';
import { randomUUID } from 'crypto';

// ─── UIA Condition Types ─────────────────────────────────────

type UIAControlType =
  | 'Button' | 'Calendar' | 'CheckBox' | 'ComboBox'
  | 'Custom' | 'DataGrid' | 'DataItem' | 'Document'
  | 'Edit' | 'Group' | 'Header' | 'HeaderItem'
  | 'Hyperlink' | 'Image' | 'List' | 'ListItem'
  | 'Menu' | 'MenuBar' | 'MenuItem' | 'Pane'
  | 'ProgressBar' | 'RadioButton' | 'ScrollBar'
  | 'Separator' | 'Slider' | 'Spinner' | 'SplitButton'
  | 'StatusBar' | 'Tab' | 'TabItem' | 'Table'
  | 'Text' | 'Thumb' | 'TitleBar' | 'ToolBar'
  | 'ToolTip' | 'Tree' | 'TreeItem' | 'Window';

// ─── UIA → UAB Type Mapping ─────────────────────────────────

const UIA_TO_ELEMENT_TYPE: Record<string, ElementType> = {
  Button: 'button',
  Calendar: 'container',
  CheckBox: 'checkbox',
  ComboBox: 'select',
  Custom: 'container',
  DataGrid: 'table',
  DataItem: 'tablerow',
  Document: 'textarea',
  Edit: 'textfield',
  Group: 'container',
  Header: 'container',
  HeaderItem: 'tablecell',
  Hyperlink: 'link',
  Image: 'image',
  List: 'list',
  ListItem: 'listitem',
  Menu: 'menu',
  MenuBar: 'menu',
  MenuItem: 'menuitem',
  Pane: 'container',
  ProgressBar: 'progressbar',
  RadioButton: 'radio',
  ScrollBar: 'scrollbar',
  Separator: 'separator',
  Slider: 'slider',
  Spinner: 'textfield',
  SplitButton: 'button',
  StatusBar: 'statusbar',
  Tab: 'container',
  TabItem: 'tab',
  Table: 'table',
  Text: 'label',
  Thumb: 'container',
  TitleBar: 'toolbar',
  ToolBar: 'toolbar',
  ToolTip: 'tooltip',
  Tree: 'tree',
  TreeItem: 'treeitem',
  Window: 'window',
};

// ─── Virtual Key Code Mapping ────────────────────────────────

const VIRTUAL_KEY_CODES: Record<string, number> = {
  // Special keys
  backspace: 0x08, tab: 0x09, enter: 0x0D, return: 0x0D,
  shift: 0x10, ctrl: 0x11, control: 0x11, alt: 0x12, menu: 0x12,
  pause: 0x13, capslock: 0x14, escape: 0x1B, esc: 0x1B,
  space: 0x20, pageup: 0x21, pagedown: 0x22,
  end: 0x23, home: 0x24,
  left: 0x25, up: 0x26, right: 0x27, down: 0x28,
  printscreen: 0x2C, insert: 0x2D, delete: 0x2E,
  // Modifier keys (Windows key)
  win: 0x5B, meta: 0x5B, lwin: 0x5B, rwin: 0x5C,
  // Function keys
  f1: 0x70, f2: 0x71, f3: 0x72, f4: 0x73,
  f5: 0x74, f6: 0x75, f7: 0x76, f8: 0x77,
  f9: 0x78, f10: 0x79, f11: 0x7A, f12: 0x7B,
  // Numpad
  numpad0: 0x60, numpad1: 0x61, numpad2: 0x62, numpad3: 0x63,
  numpad4: 0x64, numpad5: 0x65, numpad6: 0x66, numpad7: 0x67,
  numpad8: 0x68, numpad9: 0x69,
  multiply: 0x6A, add: 0x6B, subtract: 0x6D, decimal: 0x6E, divide: 0x6F,
  // Letters (A-Z = 0x41-0x5A)
  a: 0x41, b: 0x42, c: 0x43, d: 0x44, e: 0x45, f: 0x46,
  g: 0x47, h: 0x48, i: 0x49, j: 0x4A, k: 0x4B, l: 0x4C,
  m: 0x4D, n: 0x4E, o: 0x4F, p: 0x50, q: 0x51, r: 0x52,
  s: 0x53, t: 0x54, u: 0x55, v: 0x56, w: 0x57, x: 0x58,
  y: 0x59, z: 0x5A,
  // Numbers (0-9 = 0x30-0x39)
  '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
  '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
  // OEM keys
  semicolon: 0xBA, equals: 0xBB, comma: 0xBC, minus: 0xBD,
  period: 0xBE, slash: 0xBF, backquote: 0xC0,
  bracketleft: 0xDB, backslash: 0xDC, bracketright: 0xDD, quote: 0xDE,
};

// ─── UIA Control Type → Available Actions ────────────────────

function getActionsForControlType(controlType: string): ActionType[] {
  const actions: ActionType[] = ['hover', 'keypress', 'hotkey'];

  switch (controlType) {
    case 'Button':
    case 'SplitButton':
    case 'Hyperlink':
    case 'MenuItem':
    case 'TabItem':
    case 'ListItem':
    case 'TreeItem':
      actions.push('click', 'focus');
      break;
    case 'Edit':
    case 'Spinner':
      actions.push('click', 'focus', 'type', 'clear');
      break;
    case 'CheckBox':
      actions.push('click', 'focus', 'check', 'uncheck', 'toggle');
      break;
    case 'RadioButton':
      actions.push('click', 'focus', 'check');
      break;
    case 'ComboBox':
      actions.push('click', 'focus', 'select', 'expand', 'collapse');
      break;
    case 'Tree':
    case 'List':
    case 'DataGrid':
      actions.push('scroll', 'focus');
      break;
    case 'ScrollBar':
    case 'Slider':
      actions.push('scroll');
      break;
    case 'Menu':
    case 'MenuBar':
      actions.push('click', 'expand');
      break;
    case 'Window':
      actions.push('focus', 'minimize', 'maximize', 'restore', 'close', 'move', 'resize', 'screenshot');
      break;
    case 'Document':
      actions.push('click', 'rightclick', 'doubleclick', 'focus', 'type', 'clear', 'scroll');
      break;
    default:
      if (['Pane', 'Group'].includes(controlType)) {
        actions.push('focus');
      }
      break;
  }

  return actions;
}

// ─── PowerShell UIA Bridge ──────────────────────────────────

function runUIAScript(script: string, timeoutMs: number = 15000): unknown {
  const fullScript = `
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
${script}
`;
  // UIA requires interactive desktop session (Session 1)
  return runPSJsonInteractive(fullScript, timeoutMs);
}

function runRawPSScript(script: string, timeoutMs: number = 15000): string {
  // UIA requires interactive desktop session (Session 1)
  return runPSRawInteractive(script, timeoutMs);
}

function enumerateViaUIA(pid: number, maxDepth: number = 8): UIElement[] {
  const script = `
$ErrorActionPreference = 'SilentlyContinue'
function Get-UIAElements {
  param([System.Windows.Automation.AutomationElement]$element, [int]$depth, [int]$maxDepth)
  if ($depth -gt $maxDepth) { return @() }

  $result = @()
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  $children = $element.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)

  foreach ($child in $children) {
    try {
      $rect = $child.Current.BoundingRectangle
      $obj = @{
        id = "uia-$($child.Current.AutomationId)-$($child.GetHashCode())"
        name = $child.Current.Name
        controlType = $child.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', ''
        automationId = $child.Current.AutomationId
        className = $child.Current.ClassName
        isEnabled = $child.Current.IsEnabled
        hasKeyboardFocus = $child.Current.HasKeyboardFocus
        x = [math]::Round($rect.X)
        y = [math]::Round($rect.Y)
        width = [math]::Round($rect.Width)
        height = [math]::Round($rect.Height)
        children = @(Get-UIAElements -element $child -depth ($depth + 1) -maxDepth $maxDepth)
      }
      $result += $obj
    } catch { }
  }
  return $result
}

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)

$allElements = @()
foreach ($win in $appWindows) {
  $rect = $win.Current.BoundingRectangle
  $winObj = @{
    id = "uia-window-$($win.GetHashCode())"
    name = $win.Current.Name
    controlType = 'Window'
    automationId = $win.Current.AutomationId
    className = $win.Current.ClassName
    isEnabled = $win.Current.IsEnabled
    hasKeyboardFocus = $win.Current.HasKeyboardFocus
    x = [math]::Round($rect.X)
    y = [math]::Round($rect.Y)
    width = [math]::Round($rect.Width)
    height = [math]::Round($rect.Height)
    children = @(Get-UIAElements -element $win -depth 1 -maxDepth ${maxDepth})
  }
  $allElements += $winObj
}

$allElements | ConvertTo-Json -Depth 20 -Compress
`;

  try {
    const raw = runUIAScript(script, 30000) as unknown[];
    const elements = (Array.isArray(raw) ? raw : [raw]) as Array<Record<string, unknown>>;
    return elements.map(el => mapUIAToElement(el)).filter((e): e is UIElement => e !== null);
  } catch {
    return [];
  }
}

function mapUIAToElement(raw: Record<string, unknown>): UIElement | null {
  if (!raw || typeof raw !== 'object') return null;

  const controlType = (raw.controlType as string) || 'Custom';
  const type = UIA_TO_ELEMENT_TYPE[controlType] || 'container';
  const name = (raw.name as string) || '';
  const automationId = (raw.automationId as string) || '';

  const children = Array.isArray(raw.children)
    ? raw.children.map(c => mapUIAToElement(c as Record<string, unknown>)).filter((e): e is UIElement => e !== null)
    : [];

  return {
    id: (raw.id as string) || `uia-${randomUUID().substring(0, 8)}`,
    type,
    label: name,
    properties: {
      controlType,
      automationId,
      className: raw.className || '',
      hasKeyboardFocus: raw.hasKeyboardFocus || false,
    },
    bounds: {
      x: (raw.x as number) || 0,
      y: (raw.y as number) || 0,
      width: (raw.width as number) || 0,
      height: (raw.height as number) || 0,
    },
    children,
    actions: getActionsForControlType(controlType),
    visible: ((raw.width as number) || 0) > 0 && ((raw.height as number) || 0) > 0,
    enabled: (raw.isEnabled as boolean) ?? true,
    meta: { source: 'win-uia', controlType },
  };
}

// ─── Core UIA Actions ─────────────────────────────────────────

function performUIAAction(
  pid: number,
  elementId: string,
  action: string,
  params?: Record<string, unknown>,
): { success: boolean; error?: string; result?: unknown } {
  const script = `
$ErrorActionPreference = 'Stop'
$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)

function Find-Element {
  param([System.Windows.Automation.AutomationElement]$parent, [string]$targetId)
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  $all = $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  foreach ($el in $all) {
    $elId = "uia-$($el.Current.AutomationId)-$($el.GetHashCode())"
    if ($elId -eq $targetId) { return $el }
  }
  return $null
}

$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)
$target = $null
foreach ($win in $appWindows) {
  $winId = "uia-window-$($win.GetHashCode())"
  if ($winId -eq '${elementId}') {
    $target = $win
    break
  }
  $found = Find-Element -parent $win -targetId '${elementId}'
  if ($found) { $target = $found; break }
}

if (-not $target) {
  @{ success = $false; error = 'Element not found' } | ConvertTo-Json -Compress
  exit
}

# Shared Win32 mouse helper with ForceForeground support
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class UABMouse {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte sc, uint f, IntPtr e);

    public static bool ForceForeground(IntPtr target) {
      IntPtr fg = GetForegroundWindow();
      if (fg == target) return true;
      uint fgPid; uint fgT = GetWindowThreadProcessId(fg, out fgPid);
      uint curT = GetCurrentThreadId();
      keybd_event(0x12, 0, 0, IntPtr.Zero);
      keybd_event(0x12, 0, 0x02, IntPtr.Zero);
      if (fgT != curT) AttachThreadInput(curT, fgT, true);
      ShowWindow(target, 9);
      SetForegroundWindow(target);
      BringWindowToTop(target);
      if (fgT != curT) AttachThreadInput(curT, fgT, false);
      System.Threading.Thread.Sleep(100);
      return GetForegroundWindow() == target;
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

# Get window handle for foreground management
$winHandle = $null
foreach ($w in $appWindows) {
  try { $winHandle = [IntPtr]$w.Current.NativeWindowHandle; break } catch { }
}

try {
  switch ('${action}') {
    'click' {
      $clicked = $false
      try {
        $invokePattern = $target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
        if ($invokePattern) { $invokePattern.Invoke(); $clicked = $true }
      } catch { }
      if (-not $clicked) {
        try {
          $togglePattern = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
          if ($togglePattern) { $togglePattern.Toggle(); $clicked = $true }
        } catch { }
      }
      if (-not $clicked) {
        if ($winHandle) { [UABMouse]::ForceForeground($winHandle) | Out-Null }
        $target.SetFocus()
        $rect = $target.Current.BoundingRectangle
        $cx = [int]($rect.X + $rect.Width / 2)
        $cy = [int]($rect.Y + $rect.Height / 2)
        [UABMouse]::LeftClick($cx, $cy)
      }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'type' {
      $valuePattern = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
      if ($valuePattern) {
        $valuePattern.SetValue('${(params?.text || '').toString().replace(/'/g, "''")}')
        @{ success = $true } | ConvertTo-Json -Compress
      } else {
        $target.SetFocus()
        [System.Windows.Forms.SendKeys]::SendWait('${(params?.text || '').toString().replace(/'/g, "''")}')
        @{ success = $true } | ConvertTo-Json -Compress
      }
    }
    'clear' {
      $valuePattern = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
      if ($valuePattern) { $valuePattern.SetValue('') }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'focus' {
      $target.SetFocus()
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'check' {
      $togglePattern = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
      if ($togglePattern -and $togglePattern.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::On) {
        $togglePattern.Toggle()
      }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'uncheck' {
      $togglePattern = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
      if ($togglePattern -and $togglePattern.Current.ToggleState -ne [System.Windows.Automation.ToggleState]::Off) {
        $togglePattern.Toggle()
      }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'toggle' {
      $togglePattern = $target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
      if ($togglePattern) { $togglePattern.Toggle() }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'select' {
      $selPattern = $target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
      if ($selPattern) { $selPattern.Select() }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'expand' {
      $expandPattern = $target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
      if ($expandPattern) { $expandPattern.Expand() }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'collapse' {
      $expandPattern = $target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
      if ($expandPattern) { $expandPattern.Collapse() }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'rightclick' {
      if ($winHandle) { [UABMouse]::ForceForeground($winHandle) | Out-Null }
      $target.SetFocus()
      $rect = $target.Current.BoundingRectangle
      $cx = [int]($rect.X + $rect.Width / 2)
      $cy = [int]($rect.Y + $rect.Height / 2)
      [UABMouse]::RightClick($cx, $cy)
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'doubleclick' {
      if ($winHandle) { [UABMouse]::ForceForeground($winHandle) | Out-Null }
      $target.SetFocus()
      $rect = $target.Current.BoundingRectangle
      $cx = [int]($rect.X + $rect.Width / 2)
      $cy = [int]($rect.Y + $rect.Height / 2)
      [UABMouse]::DoubleClick($cx, $cy)
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'hover' {
      if ($winHandle) { [UABMouse]::ForceForeground($winHandle) | Out-Null }
      $rect = $target.Current.BoundingRectangle
      $cx = [int]($rect.X + $rect.Width / 2)
      $cy = [int]($rect.Y + $rect.Height / 2)
      [UABMouse]::MoveTo($cx, $cy)
      @{ success = $true } | ConvertTo-Json -Compress
    }
    'scroll' {
      $scrollPattern = $target.GetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern)
      if ($scrollPattern) {
        $dir = '${params?.direction || 'down'}'
        switch ($dir) {
          'down'  { $scrollPattern.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, [System.Windows.Automation.ScrollAmount]::LargeIncrement) }
          'up'    { $scrollPattern.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, [System.Windows.Automation.ScrollAmount]::LargeDecrement) }
          'right' { $scrollPattern.Scroll([System.Windows.Automation.ScrollAmount]::LargeIncrement, [System.Windows.Automation.ScrollAmount]::NoAmount) }
          'left'  { $scrollPattern.Scroll([System.Windows.Automation.ScrollAmount]::LargeDecrement, [System.Windows.Automation.ScrollAmount]::NoAmount) }
        }
      }
      @{ success = $true } | ConvertTo-Json -Compress
    }
    default {
      @{ success = $false; error = "Unknown action: ${action}" } | ConvertTo-Json -Compress
    }
  }
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;

  try {
    return runUIAScript(script, 15000) as { success: boolean; error?: string; result?: unknown };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Context Menu (compound action) ─────────────────────────

/**
 * Right-click an element and return the context menu items in one shot.
 * This avoids the focus-loss problem of separate right-click + enumerate calls.
 */
function openContextMenu(
  pid: number,
  elementId: string,
): { success: boolean; error?: string; items?: Array<{ id: string; label: string; type: string }> } {
  const script = `
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class CtxMenu {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool f);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int n);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte sc, uint f, IntPtr e);

    public static bool ForceFg(IntPtr target) {
      IntPtr fg = GetForegroundWindow();
      if (fg == target) return true;
      uint fgPid; uint fgT = GetWindowThreadProcessId(fg, out fgPid);
      uint curT = GetCurrentThreadId();
      keybd_event(0x12, 0, 0, IntPtr.Zero);
      keybd_event(0x12, 0, 0x02, IntPtr.Zero);
      if (fgT != curT) AttachThreadInput(curT, fgT, true);
      ShowWindow(target, 9);
      SetForegroundWindow(target);
      BringWindowToTop(target);
      if (fgT != curT) AttachThreadInput(curT, fgT, false);
      System.Threading.Thread.Sleep(100);
      return true;
    }

    public static void RightClick(int x, int y) {
      SetCursorPos(x, y); System.Threading.Thread.Sleep(50);
      mouse_event(0x08, 0, 0, 0, IntPtr.Zero);
      mouse_event(0x10, 0, 0, 0, IntPtr.Zero);
    }
  }
'

$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)

function Find-El {
  param([System.Windows.Automation.AutomationElement]$parent, [string]$targetId)
  $all = $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($el in $all) {
    $elId = "uia-$($el.Current.AutomationId)-$($el.GetHashCode())"
    if ($elId -eq $targetId) { return $el }
  }
  return $null
}

$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)
$target = $null
$winHandle = $null
foreach ($win in $appWindows) {
  if (-not $winHandle) { try { $winHandle = [IntPtr]$win.Current.NativeWindowHandle } catch { } }
  $winId = "uia-window-$($win.GetHashCode())"
  if ($winId -eq '${elementId}') { $target = $win; break }
  $found = Find-El -parent $win -targetId '${elementId}'
  if ($found) { $target = $found; break }
}

if (-not $target) {
  @{ success = $false; error = 'Element not found' } | ConvertTo-Json -Compress
  exit
}

# Force foreground and right-click
if ($winHandle) { [CtxMenu]::ForceFg($winHandle) | Out-Null }
$target.SetFocus()
Start-Sleep -Milliseconds 200
$rect = $target.Current.BoundingRectangle
$cx = [int]($rect.X + $rect.Width / 2)
$cy = [int]($rect.Y + $rect.Height / 2)
[CtxMenu]::RightClick($cx, $cy)
Start-Sleep -Milliseconds 600

# Now enumerate the context menu items (still in same process, no focus loss)
$menuItems = @()
$allEls = @()
foreach ($win in $appWindows) {
  $allEls += $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
}
foreach ($el in $allEls) {
  $ct = $el.Current.ControlType.ProgrammaticName -replace 'ControlType\\.', ''
  if ($ct -eq 'MenuItem' -or ($ct -eq 'Button' -and $el.Current.Name -match 'Cut|Copy|Paste|Select|Delete|Undo|Redo')) {
    $menuItems += @{
      id = "uia-$($el.Current.AutomationId)-$($el.GetHashCode())"
      label = $el.Current.Name
      type = $ct
    }
  }
}

@{ success = $true; items = $menuItems } | ConvertTo-Json -Depth 5 -Compress
`;

  try {
    const result = runUIAScript(script, 20000) as {
      success: boolean;
      error?: string;
      items?: Array<{ id: string; label: string; type: string }>;
    };
    return result;
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Keyboard Input ──────────────────────────────────────────

// Map key names to System.Windows.Forms.SendKeys format strings
const SENDKEYS_MAP: Record<string, string> = {
  // Special keys (SendKeys uses {KEYNAME} format)
  backspace: '{BACKSPACE}', tab: '{TAB}', enter: '{ENTER}', return: '{ENTER}',
  escape: '{ESC}', esc: '{ESC}', space: ' ',
  pageup: '{PGUP}', pagedown: '{PGDN}',
  end: '{END}', home: '{HOME}',
  left: '{LEFT}', up: '{UP}', right: '{RIGHT}', down: '{DOWN}',
  insert: '{INSERT}', delete: '{DELETE}',
  // Function keys
  f1: '{F1}', f2: '{F2}', f3: '{F3}', f4: '{F4}',
  f5: '{F5}', f6: '{F6}', f7: '{F7}', f8: '{F8}',
  f9: '{F9}', f10: '{F10}', f11: '{F11}', f12: '{F12}',
  // Numpad (SendKeys doesn't differentiate, use regular digits)
  numpad0: '0', numpad1: '1', numpad2: '2', numpad3: '3', numpad4: '4',
  numpad5: '5', numpad6: '6', numpad7: '7', numpad8: '8', numpad9: '9',
  multiply: '{MULTIPLY}', add: '{ADD}', subtract: '{SUBTRACT}',
  decimal: '{DECIMAL}', divide: '{DIVIDE}',
  // SendKeys special chars that need escaping
  '+': '{+}', '^': '{^}', '%': '{%}', '~': '{~}',
  '(': '{(}', ')': '{)}', '{': '{{}', '}': '{}}',
};

/**
 * Convert a key name to SendKeys format.
 * Single letters/digits pass through; special keys use SENDKEYS_MAP.
 */
function toSendKeysFormat(key: string): string | null {
  const lower = key.toLowerCase();
  // Check SendKeys map first
  if (SENDKEYS_MAP[lower]) return SENDKEYS_MAP[lower];
  // Check VK codes map (to validate it's a known key)
  if (VIRTUAL_KEY_CODES[lower] === undefined) return null;
  // Single character keys (letters, digits) — pass through directly
  if (key.length === 1) return key;
  // OEM keys
  const oemMap: Record<string, string> = {
    semicolon: ';', equals: '=', comma: ',', minus: '-',
    period: '.', slash: '/', backquote: '`',
    bracketleft: '[', backslash: '\\', bracketright: ']', quote: "'",
  };
  if (oemMap[lower]) return oemMap[lower];
  return null;
}

/**
 * Send a single keypress to a window (focus first, then keydown+keyup).
 * Uses SetForegroundWindow with thread attachment for reliable focus,
 * then SendKeys.SendWait() for modern WinUI3/UWP compatibility.
 */
function sendKeypress(pid: number, key: string): { success: boolean; error?: string } {
  const sendKey = toSendKeysFormat(key);
  if (sendKey === null) {
    return { success: false, error: `Unknown key: "${key}". Use key names like Enter, Tab, F1, a, 1, etc.` };
  }

  // Escape single quotes for PowerShell string embedding
  const escapedKey = sendKey.replace(/'/g, "''");

  try {
    const script = `
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class WinFocus {
    [DllImport("user32.dll")] public static extern IntPtr SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

    public static bool Focus(IntPtr hWnd) {
      IntPtr fg = GetForegroundWindow();
      if (fg == hWnd) return true;
      uint dummy;
      uint fgThread = GetWindowThreadProcessId(fg, out dummy);
      uint curThread = GetCurrentThreadId();
      if (fgThread != curThread) {
        AttachThreadInput(curThread, fgThread, true);
      }
      bool result = SetForegroundWindow(hWnd) != IntPtr.Zero;
      if (fgThread != curThread) {
        AttachThreadInput(curThread, fgThread, false);
      }
      return result;
    }
  }
'
$proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
if ($proc -and $proc.MainWindowHandle -ne 0) {
  [WinFocus]::Focus($proc.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 100
  [System.Windows.Forms.SendKeys]::SendWait('${escapedKey}')
  @{ success = $true } | ConvertTo-Json -Compress
} else {
  @{ success = $false; error = 'No foreground window for PID ${pid}' } | ConvertTo-Json -Compress
}
`;
    return runUIAScript(script, 10000) as { success: boolean; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Send a hotkey combination (e.g., Ctrl+Shift+S).
 * Uses SendKeys modifier syntax: ^ = Ctrl, % = Alt, + = Shift.
 */
function sendHotkey(pid: number, keys: string[]): { success: boolean; error?: string } {
  if (!keys || keys.length === 0) {
    return { success: false, error: 'No keys provided. Use keys like ["ctrl", "s"] or ["alt", "f4"].' };
  }

  // Validate all keys exist
  for (const key of keys) {
    if (VIRTUAL_KEY_CODES[key.toLowerCase()] === undefined) {
      return { success: false, error: `Unknown key in combo: "${key}".` };
    }
  }

  // Separate modifier keys from regular keys
  const modifierMap: Record<string, string> = {
    ctrl: '^', control: '^', shift: '+', alt: '%', menu: '%',
  };

  let sendKeysStr = '';
  const regularKeys: string[] = [];

  for (const key of keys) {
    const lower = key.toLowerCase();
    if (modifierMap[lower]) {
      sendKeysStr += modifierMap[lower];
    } else {
      regularKeys.push(key);
    }
  }

  // Build the regular key portion
  for (const key of regularKeys) {
    const skFormat = toSendKeysFormat(key);
    if (skFormat === null) {
      return { success: false, error: `Cannot map key "${key}" to SendKeys format.` };
    }
    // If multiple regular keys, wrap in parens so modifiers apply to all
    sendKeysStr += skFormat;
  }

  // Escape single quotes for PowerShell
  const escapedStr = sendKeysStr.replace(/'/g, "''");

  try {
    const script = `
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition '
  using System;
  using System.Runtime.InteropServices;
  public class WinFocus {
    [DllImport("user32.dll")] public static extern IntPtr SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

    public static bool Focus(IntPtr hWnd) {
      IntPtr fg = GetForegroundWindow();
      if (fg == hWnd) return true;
      uint dummy;
      uint fgThread = GetWindowThreadProcessId(fg, out dummy);
      uint curThread = GetCurrentThreadId();
      if (fgThread != curThread) {
        AttachThreadInput(curThread, fgThread, true);
      }
      bool result = SetForegroundWindow(hWnd) != IntPtr.Zero;
      if (fgThread != curThread) {
        AttachThreadInput(curThread, fgThread, false);
      }
      return result;
    }
  }
'
$proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
if ($proc -and $proc.MainWindowHandle -ne 0) {
  [WinFocus]::Focus($proc.MainWindowHandle) | Out-Null
  Start-Sleep -Milliseconds 100
  [System.Windows.Forms.SendKeys]::SendWait('${escapedStr}')
  @{ success = $true } | ConvertTo-Json -Compress
} else {
  @{ success = $false; error = 'No foreground window for PID ${pid}' } | ConvertTo-Json -Compress
}
`;
    return runUIAScript(script, 10000) as { success: boolean; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Window Management ──────────────────────────────────────

/**
 * Perform window management actions: minimize, maximize, restore, close, move, resize.
 */
function windowAction(
  pid: number,
  action: 'minimize' | 'maximize' | 'restore' | 'close' | 'move' | 'resize',
  params?: { x?: number; y?: number; width?: number; height?: number },
): { success: boolean; error?: string } {
  let psAction: string;

  switch (action) {
    case 'minimize':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); }
'
[WinMgmt]::ShowWindow($proc.MainWindowHandle, 6) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
    case 'maximize':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); }
'
[WinMgmt]::ShowWindow($proc.MainWindowHandle, 3) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
    case 'restore':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt { [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); }
'
[WinMgmt]::ShowWindow($proc.MainWindowHandle, 9) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
    case 'close':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt {
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
    public const uint WM_CLOSE = 0x0010;
  }
'
[WinMgmt]::SendMessage($proc.MainWindowHandle, [WinMgmt]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
    case 'move':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt {
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  }
'
$rect = New-Object WinMgmt+RECT
[WinMgmt]::GetWindowRect($proc.MainWindowHandle, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
[WinMgmt]::MoveWindow($proc.MainWindowHandle, ${params?.x ?? 0}, ${params?.y ?? 0}, $w, $h, $true) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
    case 'resize':
      psAction = `
Add-Type -TypeDefinition '
  using System; using System.Runtime.InteropServices;
  public class WinMgmt {
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  }
'
$rect = New-Object WinMgmt+RECT
[WinMgmt]::GetWindowRect($proc.MainWindowHandle, [ref]$rect) | Out-Null
[WinMgmt]::MoveWindow($proc.MainWindowHandle, $rect.Left, $rect.Top, ${params?.width ?? 800}, ${params?.height ?? 600}, $true) | Out-Null
@{ success = $true } | ConvertTo-Json -Compress`;
      break;
  }

  try {
    const script = `
$proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
if (-not $proc -or $proc.MainWindowHandle -eq 0) {
  @{ success = $false; error = 'No window for PID ${pid}' } | ConvertTo-Json -Compress
  exit
}
${psAction}
`;
    return runUIAScript(script, 10000) as { success: boolean; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Screenshot Capture ─────────────────────────────────────

/**
 * Capture a screenshot of a specific window.
 * Uses Win32 PrintWindow API for per-window capture (works even if partially occluded).
 * Returns the output file path.
 */
function captureWindowScreenshot(
  pid: number,
  outputPath: string,
): { success: boolean; path?: string; error?: string } {
  try {
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
  public class ScreenCapture {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, int nFlags);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public static string CaptureByHandle(IntPtr hWnd, string path) {
      RECT rect;
      if (!GetWindowRect(hWnd, out rect)) return "GetWindowRect failed";
      int w = rect.Right - rect.Left;
      int h = rect.Bottom - rect.Top;
      if (w <= 0 || h <= 0) return "Window has zero size: " + w + "x" + h;
      using (Bitmap bmp = new Bitmap(w, h)) {
        using (Graphics g = Graphics.FromImage(bmp)) {
          IntPtr hdc = g.GetHdc();
          bool ok = PrintWindow(hWnd, hdc, 2);
          g.ReleaseHdc(hdc);
          if (!ok) {
            g.CopyFromScreen(rect.Left, rect.Top, 0, 0, new Size(w, h));
          }
        }
        bmp.Save(path, ImageFormat.Png);
      }
      return "OK";
    }

    public static string CaptureByRegion(int x, int y, int w, int h, string path) {
      if (w <= 0 || h <= 0) return "Invalid region: " + w + "x" + h;
      using (Bitmap bmp = new Bitmap(w, h)) {
        using (Graphics g = Graphics.FromImage(bmp)) {
          g.CopyFromScreen(x, y, 0, 0, new Size(w, h));
        }
        bmp.Save(path, ImageFormat.Png);
      }
      return "OK";
    }
  }
'

# Ensure output directory exists
$dir = [System.IO.Path]::GetDirectoryName('${escapedPath}')
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

# Strategy 1: Try UIA to find the window with valid bounds (pick largest by area)
$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$windows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)
$captured = $false

# Find the best window (skip desktop/taskbar, prefer largest regular window)
$bestWindow = $null
$bestArea = 0
foreach ($w in $windows) {
  $rect = $w.Current.BoundingRectangle
  $name = $w.Current.Name
  $cls = $w.Current.ClassName
  if (-not $rect.IsEmpty -and $rect.Width -gt 50 -and $rect.Height -gt 50) {
    # Skip desktop (Program Manager / Progman) and taskbar (Shell_TrayWnd)
    if ($cls -eq 'Progman' -or $cls -eq 'Shell_TrayWnd' -or $cls -eq 'Shell_SecondaryTrayWnd') { continue }
    if ($name -eq 'Program Manager') { continue }
    # Skip ultra-wide bars (aspect ratio > 8:1 = likely taskbar/dock)
    if ($rect.Width / $rect.Height -gt 8) { continue }
    $area = $rect.Width * $rect.Height
    if ($area -gt $bestArea) {
      $bestArea = $area
      $bestWindow = $w
    }
  }
}

if ($bestWindow) {
  $rect = $bestWindow.Current.BoundingRectangle
  # Try PrintWindow with the UIA native handle first
  $nativeHandle = [IntPtr]$bestWindow.Current.NativeWindowHandle
  if ($nativeHandle -ne [IntPtr]::Zero) {
    $result = [ScreenCapture]::CaptureByHandle($nativeHandle, '${escapedPath}')
    if ($result -eq 'OK') {
      @{ success = $true; path = '${escapedPath}' } | ConvertTo-Json -Compress
      $captured = $true
    }
  }
  if (-not $captured) {
    # Fallback: CopyFromScreen with UIA bounds
    $x = [math]::Round($rect.X)
    $y = [math]::Round($rect.Y)
    $ww = [math]::Round($rect.Width)
    $hh = [math]::Round($rect.Height)
    $result = [ScreenCapture]::CaptureByRegion($x, $y, $ww, $hh, '${escapedPath}')
    if ($result -eq 'OK') {
      @{ success = $true; path = '${escapedPath}' } | ConvertTo-Json -Compress
      $captured = $true
    }
  }
}

if (-not $captured) {
  # Strategy 2: Fall back to MainWindowHandle (with size validation)
  $proc = Get-Process -Id ${pid} -ErrorAction SilentlyContinue
  if ($proc -and $proc.MainWindowHandle -ne 0) {
    # Check if window has reasonable dimensions before capturing
    Add-Type -TypeDefinition '
      using System; using System.Runtime.InteropServices;
      public class WinRect {
        [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L; public int T; public int R; public int B; }
        public static int[] Get(IntPtr h) { RECT r; GetWindowRect(h, out r); return new int[]{r.R-r.L, r.B-r.T}; }
      }
    '
    $dims = [WinRect]::Get($proc.MainWindowHandle)
    if ($dims[0] -lt 200 -or $dims[1] -lt 100) {
      @{ success = $false; error = "Window is too small ($($dims[0])x$($dims[1])) - it may be minimized" } | ConvertTo-Json -Compress
    } else {
      $result = [ScreenCapture]::CaptureByHandle($proc.MainWindowHandle, '${escapedPath}')
      if ($result -eq 'OK') {
        @{ success = $true; path = '${escapedPath}' } | ConvertTo-Json -Compress
        $captured = $true
      } else {
        @{ success = $false; error = $result } | ConvertTo-Json -Compress
      }
    }
  } else {
    @{ success = $false; error = 'No visible window found for PID ${pid} (window may be minimized)' } | ConvertTo-Json -Compress
  }
}
`;
    return runUIAScript(script, 20000) as { success: boolean; path?: string; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── Deep WPF Patterns ─────────────────────────────────────

/**
 * Read text content via UIA TextPattern (for rich text controls like Document, Edit).
 */
function readTextPattern(pid: number, elementId: string): { success: boolean; text?: string; error?: string } {
  try {
    const script = `
$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)

function Find-Element {
  param([System.Windows.Automation.AutomationElement]$parent, [string]$targetId)
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  $all = $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  foreach ($el in $all) {
    $elId = "uia-$($el.Current.AutomationId)-$($el.GetHashCode())"
    if ($elId -eq $targetId) { return $el }
  }
  return $null
}

$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)
$target = $null
foreach ($win in $appWindows) {
  $winId = "uia-window-$($win.GetHashCode())"
  if ($winId -eq '${elementId}') { $target = $win; break }
  $found = Find-Element -parent $win -targetId '${elementId}'
  if ($found) { $target = $found; break }
}

if (-not $target) {
  @{ success = $false; error = 'Element not found' } | ConvertTo-Json -Compress
  exit
}

try {
  $textPattern = $target.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
  if ($textPattern) {
    $text = $textPattern.DocumentRange.GetText(-1)
    @{ success = $true; text = $text } | ConvertTo-Json -Compress
  } else {
    # Fallback: try ValuePattern
    $valuePattern = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    if ($valuePattern) {
      @{ success = $true; text = $valuePattern.Current.Value } | ConvertTo-Json -Compress
    } else {
      @{ success = $true; text = $target.Current.Name } | ConvertTo-Json -Compress
    }
  }
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
    return runUIAScript(script, 15000) as { success: boolean; text?: string; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Read grid/table data via UIA GridPattern.
 */
function readGridPattern(pid: number, elementId: string): { success: boolean; grid?: unknown; error?: string } {
  try {
    const script = `
$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)

function Find-Element {
  param([System.Windows.Automation.AutomationElement]$parent, [string]$targetId)
  $cond = [System.Windows.Automation.Condition]::TrueCondition
  $all = $parent.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
  foreach ($el in $all) {
    $elId = "uia-$($el.Current.AutomationId)-$($el.GetHashCode())"
    if ($elId -eq $targetId) { return $el }
  }
  return $null
}

$appWindows = $rootEl.FindAll([System.Windows.Automation.TreeScope]::Children, $procCond)
$target = $null
foreach ($win in $appWindows) {
  $winId = "uia-window-$($win.GetHashCode())"
  if ($winId -eq '${elementId}') { $target = $win; break }
  $found = Find-Element -parent $win -targetId '${elementId}'
  if ($found) { $target = $found; break }
}

if (-not $target) {
  @{ success = $false; error = 'Element not found' } | ConvertTo-Json -Compress
  exit
}

try {
  $gridPattern = $target.GetCurrentPattern([System.Windows.Automation.GridPattern]::Pattern)
  if ($gridPattern) {
    $rows = $gridPattern.Current.RowCount
    $cols = $gridPattern.Current.ColumnCount
    $data = @()
    $maxRows = [math]::Min($rows, 50)
    for ($r = 0; $r -lt $maxRows; $r++) {
      $row = @()
      for ($c = 0; $c -lt $cols; $c++) {
        try {
          $cell = $gridPattern.GetItem($r, $c)
          $row += $cell.Current.Name
        } catch {
          $row += ''
        }
      }
      $data += ,@($row)
    }
    @{ success = $true; grid = @{ rows = $rows; cols = $cols; data = $data } } | ConvertTo-Json -Depth 10 -Compress
  } else {
    @{ success = $false; error = 'GridPattern not supported on this element' } | ConvertTo-Json -Compress
  }
} catch {
  @{ success = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
}
`;
    return runUIAScript(script, 20000) as { success: boolean; grid?: unknown; error?: string };
  } catch (err) {
    return { success: false, error: err instanceof Error ? err.message : String(err) };
  }
}

// ─── App State ──────────────────────────────────────────────

function getAppStateViaUIA(pid: number): AppState {
  const script = `
$rootEl = [System.Windows.Automation.AutomationElement]::RootElement
$procCond = New-Object System.Windows.Automation.PropertyCondition(
  [System.Windows.Automation.AutomationElement]::ProcessIdProperty, ${pid}
)
$win = $rootEl.FindFirst([System.Windows.Automation.TreeScope]::Children, $procCond)
if ($win) {
  $rect = $win.Current.BoundingRectangle
  @{
    title = $win.Current.Name
    width = [math]::Round($rect.Width)
    height = [math]::Round($rect.Height)
    x = [math]::Round($rect.X)
    y = [math]::Round($rect.Y)
    focused = $win.Current.HasKeyboardFocus
  } | ConvertTo-Json -Compress
} else {
  @{ title = ''; width = 0; height = 0; x = 0; y = 0; focused = $false } | ConvertTo-Json -Compress
}
`;

  try {
    const info = runUIAScript(script) as Record<string, unknown>;
    return {
      window: {
        title: (info.title as string) || '',
        size: { width: (info.width as number) || 0, height: (info.height as number) || 0 },
        position: { x: (info.x as number) || 0, y: (info.y as number) || 0 },
        focused: (info.focused as boolean) || false,
      },
      modals: [],
      menus: [],
    };
  } catch {
    return {
      window: { title: '', size: { width: 0, height: 0 }, position: { x: 0, y: 0 }, focused: false },
      modals: [],
      menus: [],
    };
  }
}

// ─── Plugin ─────────────────────────────────────────────────

export class WinUIAPlugin implements FrameworkPlugin {
  readonly framework = 'wpf' as const;
  readonly name = 'Windows UI Automation';

  canHandle(app: DetectedApp): boolean {
    // Accept all Windows GUI apps — UIA works as universal fallback
    // including Electron apps when CDP is unavailable
    return ['wpf', 'winui', 'dotnet', 'qt5', 'qt6', 'gtk3', 'gtk4',
            'java-swing', 'javafx', 'flutter', 'electron', 'unknown'].includes(app.framework);
  }

  async connect(app: DetectedApp): Promise<PluginConnection> {
    const state = getAppStateViaUIA(app.pid);
    if (!state.window.title && state.window.size.width === 0) {
      throw new Error(`No accessible UI found for PID ${app.pid}. The app may not have a visible window.`);
    }
    return new WinUIAConnection(app);
  }
}

// ─── Connection ─────────────────────────────────────────────

class WinUIAConnection implements PluginConnection {
  readonly app: DetectedApp;
  private _connected = true;
  private cachedTree: UIElement[] | null = null;
  private cacheTimestamp = 0;
  private readonly CACHE_TTL = 3000;
  private elementIdMap: Map<string, string> = new Map();

  constructor(app: DetectedApp) {
    this.app = app;
  }

  get connected(): boolean { return this._connected; }

  async enumerate(): Promise<UIElement[]> {
    if (this.cachedTree && Date.now() - this.cacheTimestamp < this.CACHE_TTL) {
      return this.cachedTree;
    }
    const tree = enumerateViaUIA(this.app.pid);
    this.cachedTree = tree;
    this.cacheTimestamp = Date.now();
    this.buildIdMap(tree);
    return tree;
  }

  async query(selector: ElementSelector): Promise<UIElement[]> {
    const tree = await this.enumerate();
    return this.filterTree(tree, selector, 0, selector.maxDepth);
  }

  async act(elementId: string, action: ActionType, params?: ActionParams): Promise<ActionResult> {
    // Phase 3: Handle new action types
    switch (action) {
      case 'keypress':
        return sendKeypress(this.app.pid, params?.key || params?.text || '');

      case 'hotkey':
        return sendHotkey(this.app.pid, params?.keys || []);

      case 'minimize':
      case 'maximize':
      case 'restore':
      case 'close':
        return windowAction(this.app.pid, action);

      case 'move':
        return windowAction(this.app.pid, 'move', { x: params?.x, y: params?.y });

      case 'resize':
        return windowAction(this.app.pid, 'resize', { width: params?.width, height: params?.height });

      case 'screenshot': {
        const outPath = params?.outputPath || `data/screenshots/uab-${this.app.pid}-${Date.now()}.png`;
        return captureWindowScreenshot(this.app.pid, outPath);
      }

      case 'contextmenu': {
        const result = openContextMenu(this.app.pid, elementId);
        this.cachedTree = null;
        return result;
      }

      default: {
        // Original UIA actions
        const result = performUIAAction(this.app.pid, elementId, action, {
          text: params?.text,
          value: params?.value,
          direction: params?.direction,
          amount: params?.amount,
        });
        this.cachedTree = null;
        return result;
      }
    }
  }

  async state(): Promise<AppState> {
    return getAppStateViaUIA(this.app.pid);
  }

  /** Read text content from an element (TextPattern or ValuePattern) */
  async readText(elementId: string): Promise<{ text?: string; error?: string }> {
    return readTextPattern(this.app.pid, elementId);
  }

  /** Read grid/table data from an element (GridPattern) */
  async readGrid(elementId: string): Promise<{ grid?: unknown; error?: string }> {
    return readGridPattern(this.app.pid, elementId);
  }

  async subscribe(_event: UABEventType, _callback: UABEventCallback): Promise<Subscription> {
    const subId = randomUUID();
    return {
      id: subId,
      event: _event,
      unsubscribe: () => {},
    };
  }

  async disconnect(): Promise<void> {
    this._connected = false;
    this.cachedTree = null;
    this.elementIdMap.clear();
  }

  // ─── Private Helpers ──────────────────────────────────────

  private buildIdMap(elements: UIElement[]): void {
    for (const el of elements) {
      const automationId = (el.properties.automationId as string) || '';
      this.elementIdMap.set(el.id, automationId);
      this.buildIdMap(el.children);
    }
  }

  private filterTree(elements: UIElement[], selector: ElementSelector, depth: number, maxDepth?: number): UIElement[] {
    const results: UIElement[] = [];
    const limit = selector.limit || 100;

    for (const el of elements) {
      if (results.length >= limit) break;
      if (this.matchesSelector(el, selector)) results.push(el);
      if ((!maxDepth || depth < maxDepth) && el.children.length > 0) {
        for (const child of this.filterTree(el.children, selector, depth + 1, maxDepth)) {
          if (results.length >= limit) break;
          results.push(child);
        }
      }
    }

    return results;
  }

  private matchesSelector(element: UIElement, selector: ElementSelector): boolean {
    if (selector.type && element.type !== selector.type) return false;
    if (selector.visible !== undefined && element.visible !== selector.visible) return false;
    if (selector.enabled !== undefined && element.enabled !== selector.enabled) return false;
    if (selector.label && !element.label.toLowerCase().includes(selector.label.toLowerCase())) return false;
    if (selector.labelExact && element.label !== selector.labelExact) return false;
    if (selector.labelRegex && !new RegExp(selector.labelRegex, 'i').test(element.label)) return false;
    if (selector.properties) {
      for (const [key, value] of Object.entries(selector.properties)) {
        if (element.properties[key] !== value) return false;
      }
    }
    return true;
  }
}

export default WinUIAPlugin;
