/**
 * App Registry - in-memory app profiles with JSON persistence.
 *
 * The registry remembers apps across sessions
 * without needing a database. Data lives in a Map for O(1) lookups,
 * with optional JSON file persistence for cross-session survival.
 *
 * Design constraints:
 *   - No database dependency
 *   - In-memory Map reads for lookups
 *   - Single JSON file with readable diffs
 *   - Framework-independent caller contract
 *
 * SPDX-License-Identifier: BUSL-1.1
 * Licensor: Myles Russell Hamilton
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { dirname, join } from 'path';
import type { FrameworkType, DetectedApp, ControlMethod } from './types.js';

export interface AppProfile {
  /** Stable key - lowercase executable name (e.g., "code.exe") */
  executable: string;
  /** Human-readable app name (e.g., "Visual Studio Code") */
  name: string;
  /** Last known PID (may be stale after restart) */
  pid?: number;
  /** Detected UI framework */
  framework: FrameworkType;
  /** Detection confidence 0.0–1.0 */
  confidence: number;
  /** Best control method found for this app */
  preferredMethod?: ControlMethod;
  /** Framework-specific connection params */
  connectionInfo?: Record<string, unknown>;
  /** Full executable path */
  path?: string;
  /** Window title at last detection */
  windowTitle?: string;
  /** Unix timestamp of last successful detection */
  lastSeen: number;
  /** User-defined tags for categorization */
  tags?: string[];
}

export interface RegistrySnapshot {
  version: number;
  lastScan: number;
  appCount: number;
  apps: Record<string, AppProfile>;
}

export interface RegistryOptions {
  /** Directory for profile persistence. Default: data/uab-profiles */
  profileDir?: string;
  /** Auto-save after mutations. Default: true */
  autoSave?: boolean;
}

// ─── Registry ───────────────────────────────────────────────────

export class AppRegistry {
  private apps = new Map<string, AppProfile>();
  private pidIndex = new Map<number, string>();
  private profilePath: string;
  private autoSave: boolean;
  private dirty = false;

  constructor(options?: RegistryOptions) {
    const dir = options?.profileDir || 'data/uab-profiles';
    this.profilePath = join(dir, 'registry.json');
    this.autoSave = options?.autoSave ?? true;
  }

  // ─── Persistence ──────────────────────────────────────────────

  /** Load profiles from JSON file on disk. Safe to call if file doesn't exist. */
  load(): void {
    if (!existsSync(this.profilePath)) return;

    try {
      const raw = readFileSync(this.profilePath, 'utf-8');
      const snapshot: RegistrySnapshot = JSON.parse(raw);

      if (snapshot.version !== 1) return; // Only support v1

      this.apps.clear();
      this.pidIndex.clear();

      for (const [key, profile] of Object.entries(snapshot.apps)) {
        this.apps.set(key, profile);
        if (profile.pid) {
          this.pidIndex.set(profile.pid, key);
        }
      }
      this.dirty = false;
    } catch {
      // Corrupted or unreadable — start fresh
    }
  }

  /** Persist current registry to JSON file. */
  save(): void {
    const snapshot: RegistrySnapshot = {
      version: 1,
      lastScan: Date.now(),
      appCount: this.apps.size,
      apps: Object.fromEntries(this.apps),
    };

    mkdirSync(dirname(this.profilePath), { recursive: true });
    writeFileSync(this.profilePath, JSON.stringify(snapshot, null, 2), 'utf-8');
    this.dirty = false;
  }

  private maybeSave(): void {
    if (this.autoSave && this.dirty) {
      this.save();
    }
  }

  // ─── Registration ─────────────────────────────────────────────

  /** Register a detected app into the registry. Returns the profile.
   *  For multi-process apps (same exe name), keeps the entry with a window title. */
  register(app: DetectedApp): AppProfile {
    const key = this.keyFor(app);
    const existing = this.apps.get(key);

    // Don't overwrite a windowed process with a windowless one (Electron broker/GPU fix)
    if (existing && existing.windowTitle && existing.windowTitle.length > 0
        && (!app.windowTitle || app.windowTitle.length === 0)) {
      // Still index this PID so byPid lookups work
      this.pidIndex.set(app.pid, key);
      return existing;
    }

    const profile: AppProfile = {
      executable: key,
      name: app.name,
      pid: app.pid,
      framework: app.framework,
      confidence: app.confidence,
      preferredMethod: existing?.preferredMethod,
      connectionInfo: app.connectionInfo,
      path: app.path,
      windowTitle: app.windowTitle,
      lastSeen: Date.now(),
      tags: existing?.tags,
    };

    this.apps.set(key, profile);
    this.pidIndex.set(app.pid, key);
    this.dirty = true;
    this.maybeSave();

    return profile;
  }

  /** Register multiple detected apps in bulk. Single save at the end. */
  registerAll(apps: DetectedApp[]): AppProfile[] {
    const saved = this.autoSave;
    this.autoSave = false; // Defer save

    const profiles = apps.map(app => this.register(app));

    this.autoSave = saved;
    if (this.dirty) this.save();

    return profiles;
  }

  /** Update specific fields of a profile. */
  update(executable: string, patch: Partial<AppProfile>): boolean {
    const key = executable.toLowerCase();
    const existing = this.apps.get(key);
    if (!existing) return false;

    // Update PID index if PID changed
    if (patch.pid !== undefined && existing.pid !== patch.pid) {
      if (existing.pid) this.pidIndex.delete(existing.pid);
      if (patch.pid) this.pidIndex.set(patch.pid, key);
    }

    Object.assign(existing, patch, { executable: key }); // Don't allow key mutation
    this.dirty = true;
    this.maybeSave();
    return true;
  }

  /** Remove an app profile. */
  remove(executable: string): boolean {
    const key = executable.toLowerCase();
    const existing = this.apps.get(key);
    if (!existing) return false;

    if (existing.pid) this.pidIndex.delete(existing.pid);
    this.apps.delete(key);
    this.dirty = true;
    this.maybeSave();
    return true;
  }

  // ─── Lookup ───────────────────────────────────────────────────

  /** Find profile by PID. O(1). */
  byPid(pid: number): AppProfile | undefined {
    const key = this.pidIndex.get(pid);
    return key ? this.apps.get(key) : undefined;
  }

  /** Find profiles by name (case-insensitive substring match).
   *  For multi-process apps (Electron), prefers processes with a window title. */
  byName(name: string): AppProfile[] {
    const lower = name.toLowerCase();
    const results: AppProfile[] = [];
    for (const profile of this.apps.values()) {
      if (
        profile.name.toLowerCase().includes(lower) ||
        profile.executable.includes(lower)
      ) {
        results.push(profile);
      }
    }
    // If multiple matches, prefer those with a window title
    if (results.length > 1) {
      const withWindow = results.filter(r => r.windowTitle && r.windowTitle.length > 0);
      if (withWindow.length > 0) return withWindow;
    }
    return results;
  }

  /** Find profiles by framework type. */
  byFramework(framework: FrameworkType): AppProfile[] {
    const results: AppProfile[] = [];
    for (const profile of this.apps.values()) {
      if (profile.framework === framework) results.push(profile);
    }
    return results;
  }

  /** Find profile by exact executable key. */
  byExecutable(executable: string): AppProfile | undefined {
    return this.apps.get(executable.toLowerCase());
  }

  /** Get all profiles. */
  all(): AppProfile[] {
    return Array.from(this.apps.values());
  }

  /** Number of registered apps. */
  count(): number {
    return this.apps.size;
  }

  /** Check if an app is in the registry. */
  has(executable: string): boolean {
    return this.apps.has(executable.toLowerCase());
  }

  /** Clear all profiles. */
  clear(): void {
    this.apps.clear();
    this.pidIndex.clear();
    this.dirty = true;
    this.maybeSave();
  }

  // ─── Helpers ──────────────────────────────────────────────────

  /** Generate a stable key from a detected app (lowercase executable name). */
  private keyFor(app: DetectedApp): string {
    // Extract executable name from path, fallback to app name
    const pathParts = app.path.replace(/\\/g, '/').split('/');
    const exe = pathParts[pathParts.length - 1] || app.name;
    return exe.toLowerCase();
  }

  /** Convert a registry profile back to DetectedApp format (for existing APIs). */
  toDetectedApp(profile: AppProfile): DetectedApp {
    return {
      pid: profile.pid || 0,
      name: profile.name,
      path: profile.path || profile.executable,
      framework: profile.framework,
      confidence: profile.confidence,
      connectionInfo: profile.connectionInfo,
      windowTitle: profile.windowTitle,
    };
  }
}
