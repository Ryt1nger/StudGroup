import {
  client,
  type ConfirmImageScheduleSuggestionData,
  ReviewSubgroup,
  ScheduleSuggestion,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detailQueryOptions } from '../../api/cachePolicy';
import { apiAuthorization } from '../../api/client';

export type CabinetSuggestion = ScheduleSuggestion & {
  week_start: string;
  duration: 'one_week' | 'recurring';
};
type CabinetList = {
  current_schedule_version: number;
  subgroups: ReviewSubgroup[];
  suggestions: CabinetSuggestion[];
};

/** Uses the same configured API origin as generated clients (already ending in /v1). */
export function cabinetScheduleUrl(path: string): string {
  const configured = client.getConfig().baseUrl ?? '';
  const origin = configured.replace(/\/v1\/?$/, '').replace(/\/$/, '') || window.location.origin;
  return new URL(`${origin}${path}`, window.location.origin).toString();
}

async function authorized(path: string, options: RequestInit = {}): Promise<Response> {
  const authorization = apiAuthorization();
  if (!authorization) throw new Error('Сессия завершилась. Откройте StudGroup из Telegram.');
  let response: Response;
  try {
    response = await fetch(cabinetScheduleUrl(path), {
      ...options,
      credentials: 'omit',
      headers: {
        Authorization: authorization,
        ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      },
    });
  } catch {
    throw new Error('Нет соединения с сервером');
  }
  if (!response.ok) {
    const result = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
    throw new Error(result?.error?.message ?? `Ошибка сервера: ${response.status}`);
  }
  return response;
}

export function useCabinetScheduleReview(enabled: boolean) {
  return useQuery({
    ...detailQueryOptions,
    queryKey: ['cabinet-schedule-review'],
    queryFn: async (): Promise<CabinetList> =>
      (await authorized('/v1/staff/schedule-upload')).json() as Promise<CabinetList>,
    enabled,
  });
}

export function useCabinetUpload() {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, weekStart, duration }: {
      file: File;
      weekStart: string;
      duration: 'one_week' | 'recurring';
    }): Promise<CabinetSuggestion> => {
      const body = new FormData();
      body.append('image', file);
      body.append('week_start', weekStart);
      body.append('duration', duration);
      return (await authorized('/v1/staff/schedule-upload', {
        method: 'POST',
        body,
      })).json() as Promise<CabinetSuggestion>;
    },
    onSuccess: async () => {
      await queries.invalidateQueries({ queryKey: ['cabinet-schedule-review'] });
    },
  });
}

export function useCabinetImage(id: string | null) {
  return useQuery({
    queryKey: ['cabinet-schedule-image', id],
    queryFn: async () => (await authorized(`/v1/staff/schedule-upload/${id}/image`)).blob(),
    enabled: !!id,
    staleTime: 0,
    gcTime: 60_000,
  });
}

export function useConfirmCabinetSchedule(id: string) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConfirmImageScheduleSuggestionData['body']) =>
      (await authorized(`/v1/staff/schedule-upload/${id}/confirm`, {
        method: 'POST',
        body: JSON.stringify(body),
      })).json() as Promise<{ schedule_version: number }>,
    onSuccess: async () => {
      await queries.invalidateQueries();
    },
  });
}
