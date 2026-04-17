# Supported Applications

Documented results from real-world testing. Every entry below reflects actual UAB operations performed against live applications.

---

## Test Environment

- **OS:** Windows 10/11 (64-bit)
- **Node.js:** 18+
- **UAB Version:** 1.0.0
- **Test Method:** Live agent-driven operations via CLI and programmatic API

---

## Microsoft Excel

**Framework:** Office (COM + UIA hybrid)
**Control Method:** OfficePlugin — COM automation for data operations, UIA for UI elements

### Verified Operations

| Operation | Method | Status | Notes |
|-----------|--------|--------|-------|
| Read cell value | COM `Range.Value` | ✅ Verified | Single cell by A1 notation |
| Write cell value | COM `Range.Value` | ✅ Verified | Strings, numbers, formulas |
| Read range | COM `Range.Value2` | ✅ Verified | Returns 2D array |
| Write range | COM `Range.Value2` | ✅ Verified | Batch write from array |
| Read formula | COM `Range.Formula` | ✅ Verified | Returns formula string |
| List sheets | COM `Worksheets` | ✅ Verified | Names and indices |
| Create pivot table | COM `PivotCaches` | ✅ Verified | Full pivot with fields |
| Create chart | COM `ChartObjects.Add` | ✅ Verified | Bar, line, pie charts |
| Conditional formatting | COM `FormatConditions` | ✅ Verified | Color scales, data bars |
| Apply styles | COM | ✅ Verified | Font, fill, borders, alignment |
| Navigate menus | UIA | ✅ Verified | Ribbon tabs, menu items |
| Click buttons | UIA | ✅ Verified | Ribbon buttons, dialog buttons |
| Keyboard shortcuts | UIA `SendKeys` | ✅ Verified | Ctrl+S, Ctrl+Z, etc. |

### Performance Benchmark

> **35 seconds** — Time to create a complete Excel workbook with:
> - Data population across multiple sheets
> - Pivot table with row/column/value fields
> - Chart (bar chart with formatted axes)
> - Conditional formatting (color scales + data bars)
> - Cell styling (fonts, colors, borders, number formats)
>
> All performed autonomously by an AI agent through UAB.

---

## Microsoft PowerPoint

**Framework:** Office (COM + UIA hybrid)
**Control Method:** OfficePlugin — COM for slide/shape manipulation, UIA for UI

### Verified Operations

| Operation | Method | Status | Notes |
|-----------|--------|--------|-------|
| Read slide count | COM `Slides.Count` | ✅ Verified | |
| Read slide text | COM `Shapes.TextFrame` | ✅ Verified | All text from all shapes |
| Add slides | COM `Slides.Add` | ✅ Verified | With layout selection |
| Add text boxes | COM `Shapes.AddTextbox` | ✅ Verified | Positioned, formatted |
| Add shapes | COM `Shapes.AddShape` | ✅ Verified | Rectangles, lines, etc. |
| Format text | COM `TextRange.Font` | ✅ Verified | Size, color, bold, italic |
| Set backgrounds | COM `Background.Fill` | ✅ Verified | Solid colors |
| Navigate slides | UIA | ✅ Verified | Slide panel clicks |
| Ribbon interaction | UIA | ✅ Verified | Tab switching, button clicks |

### Benchmark

> **10-slide professional presentation** built autonomously including:
> - Custom backgrounds per slide
> - Formatted text with multiple styles
> - Shape elements (rectangles, accent bars)
> - Consistent design theme across all slides

---

## Notepad

**Framework:** Win32 (unknown framework)
**Control Method:** WinUIAPlugin — Windows UI Automation (universal fallback)

### Verified Operations

| Operation | Method | Status | Notes |
|-----------|--------|--------|-------|
| Type text | UIA ValuePattern | ✅ Verified | Into main editor area |
| Read text | UIA ValuePattern | ✅ Verified | Get current content |
| Select all | SendKeys (Ctrl+A) | ✅ Verified | |
| Copy/Paste | SendKeys | ✅ Verified | Ctrl+C, Ctrl+V |
| Save file | SendKeys (Ctrl+S) | ✅ Verified | Triggers save dialog |
| Menu navigation | UIA | ✅ Verified | File, Edit, etc. |
| Window management | UIA | ✅ Verified | Minimize, maximize, move |
| Screenshot | Win32 API | ✅ Verified | Window capture |

---

## Web Browsers (Chrome, Edge, Brave)

**Framework:** Browser
**Control Method:** BrowserPlugin — Chrome DevTools Protocol (CDP)

### Verified Operations

| Operation | Method | Status | Notes |
|-----------|--------|--------|-------|
| Navigate to URL | CDP `Page.navigate` | ✅ Verified | Any URL |
| List tabs | CDP `Target.getTargets` | ✅ Verified | All open tabs |
| Switch tab | CDP `Target.activateTarget` | ✅ Verified | By tab ID |
| New tab | CDP | ✅ Verified | With optional URL |
| Close tab | CDP | ✅ Verified | By tab ID |
| Get cookies | CDP `Network.getCookies` | ✅ Verified | With filters |
| Set cookie | CDP `Network.setCookie` | ✅ Verified | Full cookie options |
| Delete cookie | CDP `Network.deleteCookies` | ✅ Verified | By name/domain |
| Clear cookies | CDP `Network.deleteCookies` | ✅ Verified | All or by domain |
| Get localStorage | CDP `Runtime.evaluate` | ✅ Verified | Key or all |
| Set localStorage | CDP `Runtime.evaluate` | ✅ Verified | Key-value |
| Delete localStorage | CDP `Runtime.evaluate` | ✅ Verified | By key or clear all |
| Get sessionStorage | CDP `Runtime.evaluate` | ✅ Verified | Key or all |
| Set sessionStorage | CDP `Runtime.evaluate` | ✅ Verified | Key-value |
| Execute JavaScript | CDP `Runtime.evaluate` | ✅ Verified | Arbitrary JS |
| Query DOM elements | CDP DOM selectors | ✅ Verified | CSS selectors |
| Click elements | CDP Input | ✅ Verified | Mouse events |
| Type text | CDP Input | ✅ Verified | Key events |
| Screenshot | CDP `Page.captureScreenshot` | ✅ Verified | PNG output |

### Supported Browsers

| Browser | Detection | CDP | Notes |
|---------|-----------|-----|-------|
| Google Chrome | ✅ Process name | ✅ | Primary target |
| Microsoft Edge | ✅ Process name | ✅ | Chromium-based |
| Brave | ✅ Process name | ✅ | Chromium-based |
| Vivaldi | ✅ Process name | ✅ | Chromium-based |
| Opera | ✅ Process name | ✅ | Chromium-based |
| Chromium | ✅ Process name | ✅ | |

> **Note:** Browser CDP requires `--remote-debugging-port`. UAB can auto-relaunch the browser with this flag if needed.

---

## Electron Applications

**Framework:** Electron
**Control Method:** ElectronPlugin — Chrome DevTools Protocol

### Electron Apps (Enhanced in v1.0.0)

UAB now correctly resolves multi-process Electron apps. When multiple processes share the same name (e.g., ChatGPT.exe × 5), UAB prefers the process with a visible window title over broker/crashpad/GPU subprocesses. Confirmed working: ChatGPT, VS Code, Slack, Discord, Teams, Notion, Obsidian.

### Verified Detection

| Application | Detection Confidence | DLL Signatures |
|-------------|---------------------|----------------|
| Visual Studio Code | 0.9 | electron.exe, chrome_elf.dll |
| Slack | 0.9 | electron.exe, libcef.dll |
| Discord | 0.9 | electron.exe |
| Notion | 0.9 | electron.exe |
| Obsidian | 0.9 | electron.exe |
| Spotify (Desktop) | 0.9 | libcef.dll |
| Microsoft Teams | 0.9 | electron.exe |

### Verified Operations (via CDP)

| Operation | Status | Notes |
|-----------|--------|-------|
| Enumerate DOM tree | ✅ Verified | Full element hierarchy |
| Query by CSS selector | ✅ Verified | Standard CSS selectors |
| Click elements | ✅ Verified | Mouse event dispatch |
| Type text | ✅ Verified | Key event dispatch |
| Read element properties | ✅ Verified | Text content, attributes |
| Screenshot | ✅ Verified | Page capture |
| Keyboard shortcuts | ✅ Verified | Hotkey combinations |

> **Note:** Electron apps may need to be launched with `--remote-debugging-port=9222` for CDP access.

---

## Qt Applications

**Framework:** Qt5 / Qt6
**Control Method:** QtPlugin → WinUIAPlugin (UIA bridge)

### Detected Apps

| Application | Qt Version | Detection |
|-------------|-----------|-----------|
| VLC Media Player | Qt5 | ✅ qt5core.dll |
| Telegram Desktop | Qt5 | ✅ qt5gui.dll |
| OBS Studio | Qt6 | ✅ qt6core.dll |
| VirtualBox | Qt5 | ✅ qt5widgets.dll |
| Wireshark | Qt5 | ✅ qt5core.dll |

### Verified Operations (via UIA)

| Operation | Status | Notes |
|-----------|--------|-------|
| Enumerate UI tree | ✅ Verified | Via Windows UIA |
| Click buttons | ✅ Verified | InvokePattern |
| Read text | ✅ Verified | ValuePattern |
| Menu navigation | ✅ Verified | Via UIA |
| Window management | ✅ Verified | Min/max/restore |

---

## Windows Explorer & System Apps

**Framework:** Win32 / WPF
**Control Method:** WinUIAPlugin

### Always Available

| Application | Framework | Notes |
|-------------|-----------|-------|
| Windows Explorer | Win32 | Always running, used as detection baseline |
| Task Manager | WPF/Win32 | UI elements accessible |
| Settings | WinUI | Modern Windows settings |
| Calculator | UWP | Standard Windows calculator |

---

## Blender

**Framework:** OpenGL (zero accessibility tree)
**Control Method:** Concerto — Keyboard (P5) for commands, OS Raw Input Injection (P6) for sculpting/painting, Screenshot for verification

### Verified Operations

| Operation | Method | Status | Notes |
|-----------|--------|--------|-------|
| Create objects | Keyboard (F3 search) | ✅ Verified | Add UV Sphere, Cube, etc. |
| Transform objects | Keyboard (G/S/R) | ✅ Verified | Move, scale, rotate with axis lock |
| Switch modes | Keyboard (Ctrl+Tab) | ✅ Verified | Object, Edit, Sculpt mode pie menu |
| Subdivide for sculpt | Keyboard (Ctrl+N) | ✅ Verified | Multiresolution modifier levels |
| Resize brush | Keyboard (F + move) | ✅ Verified | F key, mouse move, click to confirm |
| Sculpt brush strokes | P6 Drag (left button) | ✅ Verified | Draw, Clay, Smooth — continuous spatial gestures |
| Orbit camera | P6 Drag (middle button) | ✅ Verified | Middle-mouse drag to rotate view |
| Zoom | P6 Scroll | ✅ Verified | Scroll wheel injection at coordinates |
| Verify results | Screenshot | ✅ Verified | Only way to see OpenGL viewport state |

### Key Insight: The Concerto

Blender has zero accessibility tree — UIA sees only the window (childCount: 0). But with the concerto approach, full control is achieved by blending methods:

1. **Keyboard** for every command, shortcut, menu, tool switch
2. **P6 Drag** for sculpt strokes, camera orbit — operations requiring continuous mouse motion
3. **P6 Scroll** for viewport zoom
4. **Screenshot** for reading state and verifying results

No single method can control Blender alone. All four together achieve complete control.

### Achievement

> **First known AI agent to sculpt 3D geometry in Blender.** Verified 2026-03-29. Vision-guided brush strokes via OS input injection, keyboard for all commands, screenshot for verification loop.

---

## Universal Fallback

Any Windows application with a graphical window can be controlled via **WinUIAPlugin**, even if no specific framework is detected. This includes:

- Custom Win32 applications
- WPF / WinForms applications
- Legacy applications
- Any app that renders standard Windows UI controls

The `unknown` framework type automatically routes to Win-UIA.

---

## Framework Coverage Matrix

| Framework | Detect | Connect | Enumerate | Query | Act | Keyboard | Drag/Scroll | Window | Screenshot |
|-----------|--------|---------|-----------|-------|-----|----------|-------------|--------|------------|
| Electron | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Browser | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Office | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Qt 5/6 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| GTK 3/4 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Flutter | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Java | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Win32/WPF | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| OpenGL | ✅ | — | — | — | — | ✅ | ✅ | ✅ | ✅ |
