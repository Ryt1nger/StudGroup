/* eslint-disable react-refresh/only-export-components */
import { createBrowserRouter, createMemoryRouter, Navigate, useRouteError } from 'react-router';
import { TodayScreen } from './features/today/TodayScreen';
import { ru } from './i18n/ru';
import { homeworkIdFromStartParam } from './lib/deeplink';
import { AppShell } from './shell/AppShell';
import { StateButton, StateView } from './ui/Blocks';

function RouteError() {
  const error = useRouteError();
  if (import.meta.env.DEV) console.error(error);
  return (
    <StateView
      icon="alert"
      title={ru.states.errorTitle}
      body={ru.states.errorBody}
      action={<StateButton icon="refresh" onClick={() => window.location.reload()}>{ru.states.retry}</StateButton>}
    />
  );
}

function NotFound() {
  return <Navigate to="/today" replace />;
}

export function appRoutes(startParam: string | null) {
  // Keep Today (the default entry) eager. Other screens are downloaded only when
  // visited, so the first Telegram WebView load is not blocked by edit/review bundles.
  // `start_param` is an untrusted navigation hint: it can only select a homework route, and the
  // backend still enforces session, membership and ownership on the resulting request.
  const deepLinkId = homeworkIdFromStartParam(startParam);
  return [
    {
      path: '/',
      Component: AppShell,
      ErrorBoundary: RouteError,
      children: [
        { index: true, element: <Navigate to={deepLinkId ? `/homework/${deepLinkId}` : '/today'} replace /> },
        { path: 'today', Component: TodayScreen },
        { path: 'events', lazy: async () => ({ Component: (await import('./features/events/EventsScreen')).EventsScreen }) },
        { path: 'events/review', lazy: async () => ({ Component: (await import('./features/events/EventsScreen')).EventReviewScreen }) },
        { path: 'events/review/:eventId', lazy: async () => ({ Component: (await import('./features/events/EventReviewDetailScreen')).EventReviewDetailScreen }) },
        { path: 'events/:eventId', lazy: async () => ({ Component: (await import('./features/events/EventsScreen')).GroupEventScreen }) },
        { path: 'homework/:homeworkId', lazy: async () => ({ Component: (await import('./features/homework/HomeworkScreen')).HomeworkScreen }) },
        { path: 'homework/:homeworkId/edit', lazy: async () => ({ Component: (await import('./features/cardEdit/EditCardScreens')).HomeworkEditScreen }) },
        { path: 'cards/new', lazy: async () => ({ Component: (await import('./features/cardEdit/EditCardScreens')).CreateCardScreen }) },
        { path: 'tasks', lazy: async () => ({ Component: (await import('./features/tasks/TasksScreen')).TasksScreen }) },
        { path: 'schedule', lazy: async () => ({ Component: (await import('./features/schedule/ScheduleScreen')).ScheduleScreen }) },
        { path: 'schedule/manage', lazy: async () => ({ Component: (await import('./features/schedule/ScheduleManageScreen')).ScheduleManageScreen }) },
        { path: 'schedule/review', lazy: async () => ({ Component: (await import('./features/schedule/ImageScheduleReviewScreen')).ImageScheduleReviewScreen }) },
        { path: 'schedule/lesson/:lessonId', lazy: async () => ({ Component: (await import('./features/schedule/LessonScreen')).LessonScreen }) },
        { path: 'subjects', lazy: async () => ({ Component: (await import('./features/subjects/SubjectsScreen')).SubjectsScreen }) },
        { path: 'subjects/lessons/:lessonId', lazy: async () => ({ Component: (await import('./features/subjects/LessonSubjectScreen')).LessonSubjectScreen }) },
        { path: 'subjects/:subjectId', lazy: async () => ({ Component: (await import('./features/subjects/SubjectScreen')).SubjectScreen }) },
        { path: 'profile', lazy: async () => ({ Component: (await import('./features/profile/ProfileScreen')).ProfileScreen }) },
        { path: 'profile/:section', lazy: async () => ({ Component: (await import('./features/profile/ProfileScreen')).ProfileScreen }) },
        { path: 'deadlines', lazy: async () => ({ Component: (await import('./features/deadlines/DeadlinesScreen')).DeadlinesScreen }) },
        { path: 'deadlines/:deadlineId', lazy: async () => ({ Component: (await import('./features/deadlines/AcademicDeadlineScreen')).AcademicDeadlineScreen }) },
        { path: 'deadlines/:deadlineId/edit', lazy: async () => ({ Component: (await import('./features/cardEdit/EditCardScreens')).EventEditScreen }) },
        { path: '*', Component: NotFound },
      ],
    },
  ];
}

export function createAppRouter(startParam: string | null) {
  return createBrowserRouter(appRoutes(startParam));
}

export function createTestRouter(startParam: string | null, initialEntries: string[]) {
  return createMemoryRouter(appRoutes(startParam), { initialEntries });
}
