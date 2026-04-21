import type {
  ActionType,
  ConcertoMethod,
  ConcertoMethodDescriptor,
  ControlMethod,
  OperationPlan,
  OperationPlanContext,
  OperationPlanScore,
  OperationPlanRuntimeContext,
} from './types.js';

export const CONCERTO_METHODS: readonly ConcertoMethodDescriptor[] = [
  {
    id: 'chrome-extension',
    name: 'Chrome Extension Bridge',
    role: 'connection',
    speed: 'fastest',
    outcome: 'perfect',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'browser-cdp',
    name: 'Browser CDP',
    role: 'connection',
    speed: 'fast',
    outcome: 'high',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'electron-cdp',
    name: 'Electron CDP',
    role: 'connection',
    speed: 'fast',
    outcome: 'high',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'office-com+uia',
    name: 'Office COM + UIA',
    role: 'connection',
    speed: 'fast',
    outcome: 'high',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'qt-uia',
    name: 'Qt Hook via UIA',
    role: 'connection',
    speed: 'moderate',
    outcome: 'good',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'gtk-uia',
    name: 'GTK Hook via UIA',
    role: 'connection',
    speed: 'moderate',
    outcome: 'good',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'java-jab-uia',
    name: 'Java Access Bridge via UIA',
    role: 'connection',
    speed: 'moderate',
    outcome: 'good',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'flutter-uia',
    name: 'Flutter Hook via UIA',
    role: 'connection',
    speed: 'moderate',
    outcome: 'good',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'win-uia',
    name: 'Windows UI Automation',
    role: 'connection',
    speed: 'moderate',
    outcome: 'good',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'direct-api',
    name: 'Direct Application API',
    role: 'connection',
    speed: 'fast',
    outcome: 'high',
    control: 'precise',
    cost: 'free',
  },
  {
    id: 'keyboard-native',
    name: 'Keyboard Native',
    role: 'action',
    speed: 'fastest',
    outcome: 'high',
    control: 'broad',
    cost: 'free',
  },
  {
    id: 'os-input-injection',
    name: 'OS Raw Input Injection',
    role: 'action',
    speed: 'fast',
    outcome: 'perfect',
    control: 'spatial',
    cost: 'free',
  },
  {
    id: 'vision-analysis',
    name: 'Vision Analysis',
    role: 'verification',
    speed: 'slow',
    outcome: 'variable',
    control: 'broad',
    cost: 'api',
  },
  {
    id: 'vision',
    name: 'Vision Fallback',
    role: 'connection',
    speed: 'slow',
    outcome: 'variable',
    control: 'spatial',
    cost: 'api',
  },
] as const;

const DESCRIPTOR_BY_ID = new Map(CONCERTO_METHODS.map((descriptor) => [descriptor.id, descriptor]));

const SPEED_SCORES: Record<ConcertoMethodDescriptor['speed'], number> = {
  fastest: 4,
  fast: 3,
  moderate: 2,
  slow: 1,
};

const OUTCOME_SCORES: Record<ConcertoMethodDescriptor['outcome'], number> = {
  perfect: 4,
  high: 3,
  good: 2,
  variable: 1,
};

const COST_SCORES: Record<ConcertoMethodDescriptor['cost'], number> = {
  free: 2,
  api: 0,
};

type ControlPreference = ConcertoMethodDescriptor['control'];

interface OperationProfile {
  weights: {
    speed: number;
    outcome: number;
    control: number;
    cost: number;
    role: number;
    affinity: number;
  };
  preferredRole: ConcertoMethodDescriptor['role'];
  preferredControl: ControlPreference;
  candidateMethods: ConcertoMethod[];
  rationale: string;
}

interface ResolvedOperationPlanContext {
  weights?: OperationPlanContext['weights'];
  preferredRole?: OperationPlanContext['preferredRole'];
  preferredControl?: OperationPlanContext['preferredControl'];
  candidateMethods: ConcertoMethod[];
  excludedMethods: Set<ConcertoMethod>;
  boosts: Partial<Record<ConcertoMethod, number>>;
  penalties: Partial<Record<ConcertoMethod, number>>;
  runtime?: OperationPlanRuntimeContext;
  note?: string;
}

interface RuntimeAdjustments {
  boosts: Partial<Record<ConcertoMethod, number>>;
  penalties: Partial<Record<ConcertoMethod, number>>;
  excludedMethods: Set<ConcertoMethod>;
  evidence: string[];
}

const DESKTOP_BOUND_METHODS: readonly ConcertoMethod[] = [
  'office-com+uia',
  'qt-uia',
  'gtk-uia',
  'java-jab-uia',
  'flutter-uia',
  'win-uia',
  'keyboard-native',
  'os-input-injection',
] as const;

const FRAMEWORK_HOOK_METHODS: readonly ConcertoMethod[] = [
  'chrome-extension',
  'browser-cdp',
  'electron-cdp',
  'office-com+uia',
  'qt-uia',
  'gtk-uia',
  'java-jab-uia',
  'flutter-uia',
] as const;

const VISION_METHODS: readonly ConcertoMethod[] = ['vision', 'vision-analysis'] as const;

function applyAdjustment(
  bucket: Partial<Record<ConcertoMethod, number>>,
  method: ConcertoMethod,
  value: number,
): void {
  bucket[method] = (bucket[method] || 0) + value;
}

function summarizeRuntimeReason(
  runtime: OperationPlanRuntimeContext,
  activeMethod: ConcertoMethod,
): string {
  const parts: string[] = [];
  if (runtime.healthFailures && runtime.healthFailures > 0) {
    parts.push(`${runtime.healthFailures} health failure${runtime.healthFailures === 1 ? '' : 's'}`);
  }
  if (runtime.reconnectAttempts && runtime.reconnectAttempts > 0) {
    parts.push(`${runtime.reconnectAttempts} reconnect attempt${runtime.reconnectAttempts === 1 ? '' : 's'}`);
  }
  if (runtime.connectionHealthy === false) {
    parts.push('unhealthy transport state');
  }
  if (parts.length === 0) {
    return `${activeMethod} has a degraded runtime signal`;
  }
  return `${activeMethod} has ${parts.join(' and ')}`;
}

function deriveRuntimeAdjustments(
  runtime: OperationPlanRuntimeContext | undefined,
): RuntimeAdjustments {
  const adjustments: RuntimeAdjustments = {
    boosts: {},
    penalties: {},
    excludedMethods: new Set<ConcertoMethod>(),
    evidence: [],
  };

  if (!runtime) {
    return adjustments;
  }

  if (runtime.visionAvailable === false) {
    for (const method of VISION_METHODS) {
      adjustments.excludedMethods.add(method);
    }
    adjustments.evidence.push('Vision-backed transports are excluded because no vision backend is available.');
  }

  if (runtime.hasDesktop === false) {
    for (const method of DESKTOP_BOUND_METHODS) {
      adjustments.excludedMethods.add(method);
    }
    adjustments.evidence.push('Desktop-bound transports are excluded because no interactive desktop is reachable.');
  } else if (runtime.needsBridge) {
    for (const method of DESKTOP_BOUND_METHODS) {
      applyAdjustment(adjustments.penalties, method, 3);
    }
    adjustments.evidence.push('Desktop-bound transports are penalized because they must cross the Session 0 bridge.');
  }

  if (runtime.mode === 'container') {
    for (const method of DESKTOP_BOUND_METHODS) {
      applyAdjustment(adjustments.penalties, method, 2);
    }
    adjustments.evidence.push('Container mode adds a penalty to desktop-native transports.');
  }

  if (runtime.directApiAvailable) {
    applyAdjustment(adjustments.boosts, 'direct-api', 6);
    adjustments.evidence.push('Direct API transport is available at runtime and receives a preference boost.');
  }

  if (typeof runtime.frameworkConfidence === 'number' && runtime.frameworkConfidence < 0.75) {
    for (const method of FRAMEWORK_HOOK_METHODS) {
      if (method === runtime.activeMethod) {
        continue;
      }
      applyAdjustment(adjustments.penalties, method, 3);
    }
    adjustments.evidence.push(
      `Framework confidence is ${runtime.frameworkConfidence.toFixed(2)}, so inactive framework hooks are slightly demoted.`,
    );
  }

  if (runtime.activeMethod) {
    applyAdjustment(adjustments.boosts, runtime.activeMethod, 4);
    adjustments.evidence.push(`The active route (${runtime.activeMethod}) receives a confidence boost because it is already connected.`);

    const healthPenalty =
      ((runtime.healthFailures || 0) * 4) +
      ((runtime.reconnectAttempts || 0) * 3) +
      ((runtime.healthFailures || 0) > 0 ? 3 : 0) +
      (runtime.connectionHealthy === false ? 4 : 0);
    if (healthPenalty > 0) {
      applyAdjustment(adjustments.penalties, runtime.activeMethod, healthPenalty);
      adjustments.evidence.push(`The active route is penalized because ${summarizeRuntimeReason(runtime, runtime.activeMethod)}.`);
    }
  }

  if (runtime.preferredMethodOrder && runtime.preferredMethodOrder.length > 0) {
    runtime.preferredMethodOrder.forEach((method, index) => {
      const boost = Math.max(0, 2 - index);
      if (boost > 0) {
        applyAdjustment(adjustments.boosts, method, boost);
      }
    });
    adjustments.evidence.push('Live route order contributes a small runtime preference to earlier cascade methods.');
  }

  return adjustments;
}

const DEFAULT_PROFILE: OperationProfile = {
  weights: {
    speed: 2,
    outcome: 4,
    control: 4,
    cost: 1,
    role: 2,
    affinity: 2,
  },
  preferredRole: 'connection',
  preferredControl: 'precise',
  candidateMethods: [],
  rationale: 'Choose the method with the strongest balance of outcome quality, precise control, and low cost.',
};

function scoreControl(methodControl: ConcertoMethodDescriptor['control'], preferredControl: ControlPreference): number {
  if (methodControl === preferredControl) {
    return 4;
  }

  if (preferredControl === 'precise') {
    if (methodControl === 'spatial') {
      return 2;
    }
    return 1;
  }

  if (preferredControl === 'spatial') {
    if (methodControl === 'precise') {
      return 2;
    }
    return 1;
  }

  if (methodControl === 'precise') {
    return 3;
  }

  if (methodControl === 'spatial') {
    return 2;
  }

  return 1;
}

function scoreRole(
  role: ConcertoMethodDescriptor['role'],
  preferredRole: ConcertoMethodDescriptor['role'],
): number {
  if (role === preferredRole) {
    return 3;
  }

  if (preferredRole === 'verification' && role === 'connection') {
    return 1;
  }

  if (preferredRole === 'action' && role === 'connection') {
    return 1;
  }

  if (preferredRole === 'connection' && role === 'action') {
    return 1;
  }

  return 0;
}

function actionAffinity(action: ActionType | 'describe', method: ConcertoMethod): { score: number; note?: string } {
  if ((action === 'keypress' || action === 'hotkey') && method === 'keyboard-native') {
    return { score: 4, note: 'native keyboard dispatch matches the operation semantics exactly' };
  }

  if ((action === 'drag' || action === 'scroll') && method === 'os-input-injection') {
    return { score: 4, note: 'raw input preserves continuous spatial gestures' };
  }

  if (action === 'describe' && method === 'vision-analysis') {
    return { score: 4, note: 'visual analysis is purpose-built for describe workflows' };
  }

  if (action === 'screenshot' && method === 'vision-analysis') {
    return { score: 1, note: 'vision analysis is useful after capture but not ideal for primary screenshot transport' };
  }

  if (action === 'screenshot' && method === 'vision') {
    return { score: 1, note: 'vision can recover screenshots, but it is slower and API-backed' };
  }

  if (method === 'vision' || method === 'vision-analysis') {
    return { score: -1, note: 'vision methods are reserved for cases where framework hooks are weaker or unavailable' };
  }

  return { score: 0 };
}

function buildProfile(action: ActionType | 'describe'): OperationProfile {
  if (action === 'keypress' || action === 'hotkey') {
    return {
      weights: {
        speed: 4,
        outcome: 3,
        control: 2,
        cost: 1,
        role: 3,
        affinity: 4,
      },
      preferredRole: 'action',
      preferredControl: 'broad',
      candidateMethods: ['keyboard-native'],
      rationale: 'Keyboard commands prioritize dispatch speed and direct key synthesis over framework-specific routing.',
    };
  }

  if (action === 'drag' || action === 'scroll') {
    return {
      weights: {
        speed: 2,
        outcome: 4,
        control: 4,
        cost: 1,
        role: 3,
        affinity: 4,
      },
      preferredRole: 'action',
      preferredControl: 'spatial',
      candidateMethods: ['os-input-injection', 'vision'],
      rationale: 'Spatial gestures prioritize continuous pointer control and reliable gesture reproduction.',
    };
  }

  if (action === 'describe') {
    return {
      weights: {
        speed: 1,
        outcome: 4,
        control: 2,
        cost: 1,
        role: 4,
        affinity: 4,
      },
      preferredRole: 'verification',
      preferredControl: 'broad',
      candidateMethods: ['vision-analysis', 'vision'],
      rationale: 'Describe workflows prioritize visual comprehension over transport speed because the goal is inspection, not mutation.',
    };
  }

  if (action === 'screenshot') {
    return {
      weights: {
        speed: 3,
        outcome: 4,
        control: 3,
        cost: 1,
        role: 2,
        affinity: 2,
      },
      preferredRole: 'connection',
      preferredControl: 'precise',
      candidateMethods: ['vision-analysis', 'vision'],
      rationale: 'Screenshots prefer the active framework transport first, then fall back to image-centric capture and analysis paths.',
    };
  }

  return DEFAULT_PROFILE;
}

function resolveContext(context?: OperationPlanContext): ResolvedOperationPlanContext {
  return {
    weights: context?.weights,
    preferredRole: context?.preferredRole,
    preferredControl: context?.preferredControl,
    candidateMethods: context?.candidateMethods || [],
    excludedMethods: new Set(context?.excludedMethods || []),
    boosts: context?.boosts || {},
    penalties: context?.penalties || {},
    runtime: context?.runtime,
    note: context?.note,
  };
}

function applyContext(
  baseProfile: OperationProfile,
  context: ResolvedOperationPlanContext,
): OperationProfile {
  return {
    weights: {
      ...baseProfile.weights,
      ...(context.weights || {}),
    },
    preferredRole: context.preferredRole || baseProfile.preferredRole,
    preferredControl: context.preferredControl || baseProfile.preferredControl,
    candidateMethods: uniqueMethods([
      ...baseProfile.candidateMethods,
      ...context.candidateMethods,
    ]),
    rationale: context.note
      ? `${baseProfile.rationale} Runtime context: ${context.note}`
      : baseProfile.rationale,
  };
}

function uniqueMethods(methods: ConcertoMethod[]): ConcertoMethod[] {
  const seen = new Set<ConcertoMethod>();
  const ordered: ConcertoMethod[] = [];
  for (const method of methods) {
    if (!seen.has(method)) {
      seen.add(method);
      ordered.push(method);
    }
  }
  return ordered;
}

function scoreCandidate(
  action: ActionType | 'describe',
  method: ConcertoMethod,
  profile: OperationProfile,
  context: ResolvedOperationPlanContext,
): OperationPlanScore {
  const descriptor = DESCRIPTOR_BY_ID.get(method);
  if (!descriptor) {
    throw new Error(`Unknown Concerto method: ${method}`);
  }

  const controlScore = scoreControl(descriptor.control, profile.preferredControl);
  const roleScore = scoreRole(descriptor.role, profile.preferredRole);
  const affinity = actionAffinity(action, method);
  const score =
    SPEED_SCORES[descriptor.speed] * profile.weights.speed +
    OUTCOME_SCORES[descriptor.outcome] * profile.weights.outcome +
    controlScore * profile.weights.control +
    COST_SCORES[descriptor.cost] * profile.weights.cost +
    roleScore * profile.weights.role +
    affinity.score * profile.weights.affinity +
    (context.boosts[method] || 0) -
    (context.penalties[method] || 0);

  const notes: string[] = [];
  if (descriptor.role === profile.preferredRole) {
    notes.push(`matches preferred ${profile.preferredRole} role`);
  }
  if (descriptor.control === profile.preferredControl) {
    notes.push(`matches ${profile.preferredControl} control requirement`);
  }
  if (descriptor.cost === 'free') {
    notes.push('avoids API cost');
  }
  if (affinity.note) {
    notes.push(affinity.note);
  }
  if ((context.boosts[method] || 0) > 0) {
    notes.push(`received runtime preference boost (+${context.boosts[method]})`);
  }
  if ((context.penalties[method] || 0) > 0) {
    notes.push(`received runtime penalty (-${context.penalties[method]})`);
  }

  return {
    method,
    score,
    role: descriptor.role,
    dimensions: {
      speed: descriptor.speed,
      outcome: descriptor.outcome,
      control: descriptor.control,
      cost: descriptor.cost,
    },
    notes,
  };
}

function summarizeTopDrivers(score: OperationPlanScore): string {
  if (score.notes.length === 0) {
    return `${score.method} produced the highest aggregate score.`;
  }
  return `${score.method} won because it ${score.notes.slice(0, 3).join(', ')}.`;
}

export function getConcertoMethodInventory(): ConcertoMethodDescriptor[] {
  return [...CONCERTO_METHODS];
}

export function planOperation(
  connectionMethod: ControlMethod,
  action: ActionType | 'describe',
  connectionFallbacks: ControlMethod[] = [],
  context: OperationPlanContext = {},
): OperationPlan {
  const resolvedContext = resolveContext(context);
  const profile = applyContext(buildProfile(action), resolvedContext);
  const runtimeAdjustments = deriveRuntimeAdjustments(resolvedContext.runtime);
  const availableMethods = new Set(resolvedContext.runtime?.availableMethods || []);
  const candidates = uniqueMethods([
    connectionMethod,
    ...connectionFallbacks,
    ...profile.candidateMethods,
  ]).filter((method) => {
    if (resolvedContext.excludedMethods.has(method) || runtimeAdjustments.excludedMethods.has(method)) {
      return false;
    }
    if (availableMethods.size > 0 && !availableMethods.has(method)) {
      return false;
    }
    return true;
  });

  const scoringTrace = candidates
    .map((candidate) => scoreCandidate(action, candidate, profile, {
      ...resolvedContext,
      boosts: {
        ...runtimeAdjustments.boosts,
        ...resolvedContext.boosts,
      },
      penalties: {
        ...runtimeAdjustments.penalties,
        ...resolvedContext.penalties,
      },
    }))
    .sort((left, right) => right.score - left.score);

  if (scoringTrace.length === 0) {
    throw new Error(`No Concerto candidates available for action "${action}"`);
  }

  const primary = scoringTrace[0];
  const fallbackMethods = scoringTrace.slice(1).map((candidate) => candidate.method);
  const descriptor = DESCRIPTOR_BY_ID.get(primary.method);
  const rationale = [
    profile.rationale,
    ...runtimeAdjustments.evidence,
    summarizeTopDrivers(primary),
    descriptor
      ? `Runtime score: ${primary.score} (${descriptor.speed} speed, ${descriptor.outcome} outcome, ${descriptor.control} control, ${descriptor.cost} cost).`
      : `Runtime score: ${primary.score}.`,
  ].join(' ');

  return {
    action,
    primaryMethod: primary.method,
    fallbackMethods,
    rationale,
    scoringTrace,
    runtimeEvidence: runtimeAdjustments.evidence,
  };
}
