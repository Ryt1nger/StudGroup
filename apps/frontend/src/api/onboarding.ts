/** Secure first-run flows use the existing in-memory Telegram web session. */
import { client } from '@studgroup/shared-types';
import type { OnboardingState, OnboardingStep } from '@studgroup/shared-types';
import { apiAuthorization } from './client';

const base = () => (client.getConfig().baseUrl ?? '/v1').replace(/\/$/, '');
type Stage = OnboardingStep;
export type { OnboardingState } from '@studgroup/shared-types';

export interface StudentSplit {
  split_id: string;
  subject: { id: string; name: string };
  options: { id: string; name: string }[];
  selected_subgroup_id: string | null;
}

export async function onboardingRequest<T>(url: string, options: RequestInit = {}): Promise<T> {
  const authorization = apiAuthorization();
  if (!authorization) throw new Error('Войдите через Telegram');
  const response = await fetch(`${base()}${url}`, {
    ...options,
    cache: 'no-store',
    credentials: 'omit',
    headers: {
      Authorization: authorization,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error('Сессия истекла. Откройте Mini App заново из Telegram.');
    if (response.status === 403) throw new Error('Нет доступа к выбранной группе или необходимо подтвердить согласия.');
    if (response.status === 503) throw new Error('Функция временно недоступна. Повторите попытку позже.');
    throw new Error('Не удалось сохранить этап регистрации. Проверьте соединение.');
  }
  return response.json() as Promise<T>;
}

export const getOnboarding = () => onboardingRequest<OnboardingState>('/onboarding');
export const setOnboardingStep = (step: Exclude<Stage, 'complete'>) =>
  onboardingRequest<{ step: Stage; completed: boolean }>('/onboarding/progress', {
    method: 'PUT', body: JSON.stringify({ step }),
  });
export const completeOnboarding = () =>
  onboardingRequest<{ completed: boolean }>('/onboarding/complete', { method: 'POST' });

export const getStudentSplits = () =>
  onboardingRequest<{ items: StudentSplit[] }>('/subgroups');
export const selectStudentSplit = (splitId: string, subgroupId: string) =>
  onboardingRequest<{ items: StudentSplit[] }>(`/subgroups/${encodeURIComponent(splitId)}`, {
    method: 'PUT', body: JSON.stringify({ subgroup_id: subgroupId }),
  });
