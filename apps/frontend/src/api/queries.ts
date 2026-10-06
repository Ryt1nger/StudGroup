import {
  bootstrapSession,
  getHomework,
  getSchedule,
  listHomework,
  getToday,
  getLessonSubject,
  listAcademicDeadlines,
  getAcademicDeadline,
  recheckMembership,
  setHomeworkCompletion,
  type HomeworkDetail,
  type HomeworkListResponse,
  type ScheduleResponse,
  type SessionBootstrapResponse,
  type SessionContext,
  type TodayResponse,
  type LessonSubjectResponse,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { unwrap, isApiRequestError } from './errors';
import { queryKeys } from './keys';
import { detailQueryOptions, scheduleQueryOptions, todayQueryOptions } from './cachePolicy';

export const HOMEWORK_PAGE_SIZE = 50;
export type HomeworkListFilter = NonNullable<NonNullable<Parameters<typeof listHomework>[0]>['query']>['filter'] & string;

export const api = {
  bootstrap: (initData: string): Promise<SessionBootstrapResponse> =>
    unwrap(bootstrapSession({ body: { init_data: initData } })),
  recheck: (): Promise<SessionContext> => unwrap(recheckMembership()),
  today: (day: 'today' | 'tomorrow' = 'today'): Promise<TodayResponse> => unwrap(getToday({ query: { day } })),
  lessonSubject: (id: string): Promise<LessonSubjectResponse> => unwrap(getLessonSubject({ path: { lesson_id: id } })),
  deadlines: (archive = false) => unwrap(listAcademicDeadlines({ query: { archive } })),
  event: (id: string) => unwrap(getAcademicDeadline({ path: { deadline_id: id } })),
  schedule: (start: string, end: string): Promise<ScheduleResponse> => unwrap(getSchedule({ query: { start, end } })),
  listHomework: (filter: HomeworkListFilter, cursor?: string): Promise<HomeworkListResponse> =>
    unwrap(listHomework({ query: { filter, limit: HOMEWORK_PAGE_SIZE, ...(cursor ? { cursor } : {}) } })),
  homework: (id: string): Promise<HomeworkDetail> => unwrap(getHomework({ path: { homework_id: id } })),
  setCompletion: (id: string, completed: boolean, expectedRevision: number): Promise<HomeworkDetail> =>
    unwrap(
      setHomeworkCompletion({
        path: { homework_id: id },
        body: { completed, expected_revision: expectedRevision },
      }),
    ),
};

export function useTodayQuery(day: 'today' | 'tomorrow' = 'today') {
  return useQuery({ queryKey: [...queryKeys.today, day], queryFn: () => api.today(day), ...todayQueryOptions });
}

export function useLessonSubjectQuery(id: string) {
  return useQuery({ queryKey: queryKeys.lessonSubject(id), queryFn: () => api.lessonSubject(id), ...detailQueryOptions });
}

export function useAcademicDeadlinesQuery(archive = false) {
  return useQuery({ queryKey: ['academic-deadlines', archive], queryFn: () => api.deadlines(archive), ...todayQueryOptions });
}

export function useAcademicDeadlineQuery(id: string) {
  return useQuery({ queryKey: ['academic-deadline', id], queryFn: () => api.event(id), ...detailQueryOptions });
}

export function useScheduleQuery(start: string, end: string, enabled = true) {
  return useQuery({ queryKey: queryKeys.schedule(start, end), queryFn: () => api.schedule(start, end), enabled, ...scheduleQueryOptions });
}

export function useHomeworkQuery(id: string) {
  return useQuery({ queryKey: queryKeys.homework(id), queryFn: () => api.homework(id), ...detailQueryOptions });
}

interface CompletionVars {
  completed: boolean;
  expectedRevision: number;
}

/**
 * Personal "Выполнено мной" write. Optimistic on the detail card; rolled back on failure.
 * A 409 `revision_conflict` forces a refetch before the user can repeat the action.
 */
export function useCompletionMutation(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ completed, expectedRevision }: CompletionVars) => api.setCompletion(id, completed, expectedRevision),
    onMutate: async ({ completed }) => {
      await queryClient.cancelQueries({ queryKey: queryKeys.homework(id) });
      const previous = queryClient.getQueryData<HomeworkDetail>(queryKeys.homework(id));
      if (previous) {
        queryClient.setQueryData<HomeworkDetail>(queryKeys.homework(id), {
          ...previous,
          my_state: {
            ...previous.my_state,
            completion: completed ? 'completed' : 'pending',
          },
        });
      }
      return { previous };
    },
    onError: async (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(queryKeys.homework(id), context.previous);
      if (isApiRequestError(error) && error.code === 'revision_conflict') {
        await queryClient.invalidateQueries({ queryKey: queryKeys.homework(id) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.lessonSubjects });
        restartHomeworkLists(queryClient);
      }
    },
    onSuccess: (detail) => {
      queryClient.setQueryData(queryKeys.homework(id), detail);
      // The backend owns feed composition: completing a card can remove or return it.
      void queryClient.invalidateQueries({ queryKey: queryKeys.today });
      void queryClient.invalidateQueries({ queryKey: queryKeys.lessonSubjects });
      // Cursors are not valid across mutations: drop every loaded page and restart from the first.
      restartHomeworkLists(queryClient);
    },
  });
}

/** Drops loaded pages and cursor chains of every Tasks filter; active lists refetch their first page. */
export function restartHomeworkLists(queryClient: QueryClient): void {
  void queryClient.resetQueries({ queryKey: queryKeys.homeworkList });
}

export function resetLearningData(queryClient: QueryClient): void {
  queryClient.clear();
}
