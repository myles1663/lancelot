import type { ControlMethod, FrameworkHookDescriptor } from './types.js';

export const FRAMEWORK_HOOKS: Record<string, FrameworkHookDescriptor> = {
  'chrome-extension': {
    id: 'chrome-extension',
    name: 'Chrome Extension Bridge',
    frameworks: ['browser'],
    integration: 'native',
    protocol: 'WebSocket bridge',
    discoverySignals: ['Chromium process name', 'connected extension session'],
  },
  'browser-cdp': {
    id: 'browser-cdp',
    name: 'Browser CDP',
    frameworks: ['browser'],
    integration: 'native',
    protocol: 'Chrome DevTools Protocol',
    discoverySignals: ['Chromium process name', '--remote-debugging-port'],
  },
  'electron-cdp': {
    id: 'electron-cdp',
    name: 'Electron CDP',
    frameworks: ['electron'],
    integration: 'native',
    protocol: 'Chrome DevTools Protocol',
    discoverySignals: ['electron.exe', 'libcef.dll', '--remote-debugging-port'],
  },
  'office-com+uia': {
    id: 'office-com+uia',
    name: 'Office COM + UIA',
    frameworks: ['office'],
    integration: 'native',
    protocol: 'PowerShell COM automation + UIA',
    discoverySignals: ['WINWORD/EXCEL/POWERPNT/OUTLOOK process name', 'Office DLL signatures'],
  },
  'qt-uia': {
    id: 'qt-uia',
    name: 'Qt UIA Bridge',
    frameworks: ['qt5', 'qt6'],
    integration: 'bridge',
    protocol: 'QAccessible → MSAA/UIA bridge',
    discoverySignals: ['Qt5/Qt6 DLLs', 'Qt window class names'],
  },
  'gtk-uia': {
    id: 'gtk-uia',
    name: 'GTK UIA Bridge',
    frameworks: ['gtk3', 'gtk4'],
    integration: 'bridge',
    protocol: 'ATK/GTK accessibility → MSAA/UIA bridge',
    discoverySignals: ['libgtk-3/libgtk-4 DLLs'],
  },
  'java-jab-uia': {
    id: 'java-jab-uia',
    name: 'Java JAB → UIA',
    frameworks: ['java-swing', 'javafx'],
    integration: 'bridge',
    protocol: 'Java Access Bridge → MSAA/UIA',
    discoverySignals: ['jvm.dll', 'java.exe/javaw.exe', 'SunAwtFrame/Glass windows'],
  },
  'flutter-uia': {
    id: 'flutter-uia',
    name: 'Flutter UIA Bridge',
    frameworks: ['flutter'],
    integration: 'bridge',
    protocol: 'Flutter semantics → UIA',
    discoverySignals: ['flutter_windows.dll', 'FlutterDesktopView class'],
  },
  'win-uia': {
    id: 'win-uia',
    name: 'Windows UI Automation',
    frameworks: ['wpf', 'dotnet', 'unknown'],
    integration: 'fallback',
    protocol: 'Native Windows UI Automation',
    discoverySignals: ['visible window handle', 'UIA tree availability'],
  },
};

export function describeControlMethod(method: ControlMethod): FrameworkHookDescriptor | null {
  return FRAMEWORK_HOOKS[method] ?? null;
}
