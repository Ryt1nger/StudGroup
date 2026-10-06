/* eslint-disable react-refresh/only-export-components */
import { createBrowserRouter, createMemoryRouter, Navigate, useRouteError } from 'react-router';
import { TodayScreen } from './features/today/TodayScreen';
import { HomeworkScreen } from './features/homework/HomeworkScreen';
import { ru } from './i18n/ru';
import { homeworkIdFromStartParam } from './lib/deeplink';
import { AppShell } from './shell/AppShell';
import { TasksScreen } from './features/tasks/TasksScreen';
import { ScheduleScreen } from './features/schedule/ScheduleScreen';
import { LessonSubjectScreen } from './features/subjects/LessonSubjectScreen';
import { DeadlinesScreen } from './features/deadlines/DeadlinesScreen';
import { AcademicDeadlineScreen } from './features/deadlines/AcademicDeadlineScreen';
import { PreparedSection } from './shell/PreparedSection';
import { StateView } from './ui/StateView';
import { Button } from './ui/Button';
import { AlertCircle } from './ui/icons';

function RouteError() {
  const error = useRouteError();
  if (import.meta.env.DEV) console.error(error);
  return (
    <StateView
      role="alert"
      icon={<AlertCircle />}
      title={ru.states.errorTitle}
      body={ru.states.errorBody}
      action={<Button onClick={() => window.location.reload()}>{ru.states.retry}</Button>}
    />
  );
}

function NotFound() {
  return <Navigate to="/today" replace />;
}

export function appRoutes(startParam: string | null) {
  // `start_param` is an untrusted navigation hint: it can only select a homework route, and the
  // backend still enforces session, membership and ownership on the resulting request.
  const deepLinkId = homeworkIdFromStartParam(startParam);
  const devRoutes = import.meta.env.DEV
    ? [{ path: 'preview/schedule', lazy: async () => ({ Component: (await import('./features/schedule/SchedulePreview')).SchedulePreview }) }]
    : [];
  return [
    {
      path: '/',
      Component: AppShell,
      ErrorBoundary: RouteError,
      children: [
        { index: true, element: <Navigate to={deepLinkId ? `/homework/${deepLinkId}` : '/today'} replace /> },
        { path: 'today', Component: TodayScreen },
        { path: 'homework/:homeworkId', Component: HomeworkScreen },
        { path: 'tasks', Component: TasksScreen },
        { path: 'schedule', Component: ScheduleScreen },
        { path: 'subjects', element: <PreparedSection title={ru.nav.subjects} /> },
        { path: 'subjects/lessons/:lessonId', Component: LessonSubjectScreen },
        { path: 'deadlines', Component: DeadlinesScreen },
        { path: 'deadlines/:deadlineId', Component: AcademicDeadlineScreen },
        ...devRoutes,
        { path: '*', Component: NotFound },
      ],
    },
  ];
}

export function createAppRouter(startParam: string | null) {
  return createBrowserRouter(appRoutes(startParam), {
    basename: import.meta.env.MODE === 'demo' ? import.meta.env.BASE_URL : undefined,
  });
}

export function createTestRouter(startParam: string | null, initialEntries: string[]) {
  return createMemoryRouter(appRoutes(startParam), { initialEntries });
}
