/**
 * UIA metadata mapping used by the Windows accessibility plugin.
 */

import type { ActionType, ElementType } from '../../types.js';


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

export const UIA_TO_ELEMENT_TYPE: Record<string, ElementType> = {
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

export const VIRTUAL_KEY_CODES: Record<string, number> = {
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

export function getActionsForControlType(controlType: string): ActionType[] {
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
