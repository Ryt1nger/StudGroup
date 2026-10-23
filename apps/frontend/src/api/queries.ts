import {
  bootstrapSession,
  getAttachmentImage,
  getHomework,
  getSchedule,
  listHomework,
  getToday,
  getLessonSubject,
  listSubjects,
  listAcademicDeadlines,
  getAcademicDeadline,
  editHomework,
  getHomeworkFileLink,
  getDeadlineFileLink,
  editAcademicDeadline,
  recheckMembership,
  setHomeworkCompletion,
  getProfile,
  updateProfile,
  type UpdateProfileRequest,
  type UserProfile,
  type EditAcademicDeadlineRequest,
  type EditHomeworkRequest,
  type HomeworkDetail,
  type HomeworkListResponse,
  type ScheduleResponse,
  type SessionBootstrapResponse,
  type SessionContext,
  type TodayResponse,
  type LessonSubjectResponse,
  type SubjectListResponse,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { unwrap, isApiRequestError } from './errors';
import { queryKeys } from './keys';
import { detailQueryOptions, scheduleQueryOptions, todayQueryOptions } from './cachePolicy';

export const HOMEWORK_PAGE_SIZE = 50;
export type HomeworkListFilter = NonNullable<
  NonNullable<Parameters<typeof listHomework>[0]>['query']
>['filter'] &
  string;

export const api = {
  bootstrap: (initData: string): Promise<SessionBootstrapResponse> =>
    unwrap(bootstrapSession({ body: { init_data: initData } })),
  recheck: (): Promise<SessionContext> => unwrap(recheckMembership()),
  today: (day: 'today' | 'tomorrow' = 'today'): Promise<TodayResponse> =>
    unwrap(getToday({ query: { day } })),
  lessonSubject: (id: string): Promise<LessonSubjectResponse> =>
    unwrap(getLessonSubject({ path: { lesson_id: id } })),
  subjects: (): Promise<SubjectListResponse> => unwrap(listSubjects()),
  deadlines: (archive = false, review = false) =>
    unwrap(listAcademicDeadlines({ query: { archive, ...(review ? { review: true } : {}) } })),
  event: (id: string) => unwrap(getAcademicDeadline({ path: { deadline_id: id } })),
  schedule: (start: string, end: string): Promise<ScheduleResponse> =>
    unwrap(getSchedule({ query: { start, end } })),
  listHomework: (filter: HomeworkListFilter, cursor?: string): Promise<HomeworkListResponse> =>
    unwrap(listHomework({ query: { filter, limit: HOMEWORK_PAGE_SIZE, ...(cursor ? { cursor } : {}) } })),
  attachmentImage: (id: string): Promise<Blob> =>
    unwrap(getAttachmentImage({ path: { attachment_id: id }, parseAs: 'blob' })) as Promise<Blob>,
  homework: (id: string): Promise<HomeworkDetail> => unwrap(getHomework({ path: { homework_id: id } })),
  editHomework: (id: string, body: EditHomeworkRequest): Promise<HomeworkDetail> =>
    unwrap(editHomework({ path: { homework_id: id }, body })),
  homeworkFileLink: (id: string): Promise<string> =>
    unwrap(getHomeworkFileLink({ path: { homework_id: id } })).then((r) => r.url),
  eventFileLink: (id: string): Promise<string> =>
    unwrap(getDeadlineFileLink({ path: { deadline_id: id } })).then((r) => r.url),
  editEvent: (id: string, body: EditAcademicDeadlineRequest) =>
    unwrap(editAcademicDeadline({ path: { deadline_id: id }, body })),
  setCompletion: (id: string, completed: boolean, expectedRevision: number): Promise<HomeworkDetail> =>
    unwrap(
      setHomeworkCompletion({
        path: { homework_id: id },
        body: { completed, expected_revision: expectedRevision },
      }),
    ),
};

export function useProfileQuery(options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['profile'],
    queryFn: (): Promise<UserProfile> => unwrap(getProfile()),
    staleTime: 60_000,
    enabled: options.enabled ?? true,
  });
}

/** Saves only the sent fields; the response (without the password) replaces the cached profile. */
export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: UpdateProfileRequest): Promise<UserProfile> => unwrap(updateProfile({ body })),
    onSuccess: (profile) => queryClient.setQueryData(['profile'], profile),
  });
}

export function useTodayQuery(day: 'today' | 'tomorrow' = 'today') {
  return useQuery({
    queryKey: [...queryKeys.today, day],
    queryFn: () => api.today(day),
    ...todayQueryOptions,
  });
}

export function useLessonSubjectQuery(id: string) {
  return useQuery({
    queryKey: queryKeys.lessonSubject(id),
    queryFn: () => api.lessonSubject(id),
    enabled: id !== '',
    ...detailQueryOptions,
  });
}

export function useSubjectsQuery() {
  return useQuery({ queryKey: queryKeys.subjects, queryFn: api.subjects, ...todayQueryOptions });
}

export function useAcademicDeadlinesQuery(archive = false, review = false) {
  return useQuery({
    queryKey: ['academic-deadlines', archive, review],
    queryFn: () => api.deadlines(archive, review),
    ...todayQueryOptions,
  });
}

export function useAcademicDeadlineQuery(id: string) {
  return useQuery({
    queryKey: ['academic-deadline', id],
    queryFn: () => api.event(id),
    ...detailQueryOptions,
  });
}

export function useScheduleQuery(start: string, end: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.schedule(start, end),
    queryFn: () => api.schedule(start, end),
    enabled,
    ...scheduleQueryOptions,
  });
}

/** The bytes of a photo material; the object URL is created and revoked by the component. */
export function useAttachmentImageQuery(id: string) {
  return useQuery({
    queryKey: queryKeys.attachmentImage(id),
    queryFn: () => api.attachmentImage(id),
    staleTime: 30 * 60 * 1000,
    gcTime: 30 * 60 * 1000,
  });
}

export function useHomeworkQuery(id: string) {
  return useQuery({
    queryKey: queryKeys.homework(id),
    queryFn: () => api.homework(id),
    ...detailQueryOptions,
  });
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
    mutationFn: ({ completed, expectedRevision }: CompletionVars) =>
      api.setCompletion(id, completed, expectedRevision),
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

/**
 * Headman/deputy edit of a homework card. The server decides (403 for students, 409 when the card
 * changed); on a conflict the card is refetched so the user sees the current data before retrying.
 */
export function useEditHomeworkMutation(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: EditHomeworkRequest) => api.editHomework(id, body),
    onError: async (error) => {
      if (isApiRequestError(error) && error.code === 'revision_conflict') {
        await queryClient.invalidateQueries({ queryKey: queryKeys.homework(id) });
      }
    },
    onSuccess: (detail) => {
      queryClient.setQueryData(queryKeys.homework(id), detail);
      void queryClient.invalidateQueries({ queryKey: queryKeys.today });
      void queryClient.invalidateQueries({ queryKey: queryKeys.lessonSubjects });
      void queryClient.invalidateQueries({ queryKey: queryKeys.subjects });
      restartHomeworkLists(queryClient);
    },
  });
}

/** Headman/deputy edit of an academic event (test, control point, ...). */
export function useEditEventMutation(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: EditAcademicDeadlineRequest) => api.editEvent(id, body),
    onError: async (error) => {
      if (isApiRequestError(error) && error.code === 'revision_conflict') {
        await queryClient.invalidateQueries({ queryKey: ['academic-deadline', id] });
      }
    },
    onSuccess: (response) => {
      queryClient.setQueryData(['academic-deadline', id], response);
      void queryClient.invalidateQueries({ queryKey: ['academic-deadlines'] });
      void queryClient.invalidateQueries({ queryKey: queryKeys.today });
      void queryClient.invalidateQueries({ queryKey: queryKeys.subjects });
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
