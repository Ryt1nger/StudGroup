import { createContext, useContext } from 'react';

/** Invoked from the profile only after the server confirms completed onboarding. */
export const OnboardingPreviewContext = createContext<(() => void) | null>(null);

export function useOnboardingPreview(): (() => void) | null {
  return useContext(OnboardingPreviewContext);
}
