import {
  client,
  type ConfirmImageScheduleSuggestionData,
  type ReviewSubgroup,
  type ScheduleSuggestion,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { detailQueryOptions } from '../../api/cachePolicy';
import { apiAuthorization } from '../../api/client';

export type CabinetSuggestion = Omit<ScheduleSuggestion, 'state'> & {
  state: 'queued' | 'processing' | 'pending' | 'confirmed' | 'failed';
  progress_percent: number;
  progress_stage: string;
  can_retry: boolean;
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
    const result = (await response.json().catch(() => null)) as {
      error?: { code?: string; message?: string };
    } | null;
    // Funding and AI availability are operator-only details; keep the app error simple.
    if (result?.error?.code === 'analysis_budget_closed' ||
        result?.error?.code === 'analysis_unavailable') {
      throw new Error('Распознавание сейчас недоступно. Попробуйте позже.');
    }
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
    // Only poll when there is an active server-side job. After closing Telegram,
    // reopening the page fetches the same persisted status from the database.
    refetchInterval: (query) =>
      query.state.data?.suggestions.some(
        (item) => item.state === 'queued' || item.state === 'processing',
      ) ? 3000 : false,
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

export function useRetryCabinetUpload(id: string) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<CabinetSuggestion> =>
      (await authorized(`/v1/staff/schedule-upload/${id}/retry`, { method: 'POST' }))
        .json() as Promise<CabinetSuggestion>,
    onSuccess: async () => {
      await queries.invalidateQueries({ queryKey: ['cabinet-schedule-review'] });
    },
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
