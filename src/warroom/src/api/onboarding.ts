import { apiGet, apiPost } from './client'
import type { OnboardingStatusResponse, OnboardingCommandResponse } from '@/types/api'

/** GET /onboarding/status — Detailed onboarding snapshot */
export function fetchOnboardingStatus() {
  return apiGet<OnboardingStatusResponse>('/onboarding/status')
}

/** POST /onboarding/command — Execute a recovery command */
export function sendOnboardingCommand(command: string) {
  return apiPost<OnboardingCommandResponse>('/onboarding/command', { command })
}

/** POST /onboarding/back — Shortcut: BACK */
export function onboardingBack() {
  return apiPost<OnboardingCommandResponse>('/onboarding/back')
}

/** POST /onboarding/restart-step — Shortcut: RESTART STEP */
export function onboardingRestartStep() {
  return apiPost<OnboardingCommandResponse>('/onboarding/restart-step')
}

/** POST /onboarding/resend-code — Shortcut: RESEND CODE */
export function onboardingResendCode() {
  return apiPost<OnboardingCommandResponse>('/onboarding/resend-code')
}

/** POST /onboarding/reset — Shortcut: RESET ONBOARDING */
export function onboardingReset() {
  return apiPost<OnboardingCommandResponse>('/onboarding/reset')
}
