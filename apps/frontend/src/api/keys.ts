export const queryKeys = {
  today: ['today'] as const,
  lessonSubjects: ['lesson-subject'] as const,
  lessonSubject: (id: string) => ['lesson-subject', id] as const,
  schedule: (start: string, end: string) => ['schedule', start, end] as const,
  /** Tasks list prefix; the filter is the next key segment. */
  homeworkList: ['homework-list'] as const,
  homeworkListFor: (filter: string) => ['homework-list', filter] as const,
  homework: (id: string) => ['homework', id] as const,
};
