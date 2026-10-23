/* eslint-disable react-refresh/only-export-components */
import { createBrowserRouter, createMemoryRouter, Navigate, useRouteError } from 'react-router';
import { TodayScreen } from './features/today/TodayScreen';
import { HomeworkScreen } from './features/homework/HomeworkScreen';
import { ru } from './i18n/ru';
import { homeworkIdFromStartParam } from './lib/deeplink';
import { AppShell } from './shell/AppShell';
import { TasksScreen } from './features/tasks/TasksScreen';
import { ScheduleScreen } from './features/schedule/ScheduleScreen';
import { LessonScreen } from './features/schedule/LessonScreen';
import { SubjectsScreen } from './features/subjects/SubjectsScreen';
import { DeadlinesScreen } from './features/deadlines/DeadlinesScreen';
import { EventEditScreen, HomeworkEditScreen } from './features/cardEdit/EditCardScreens';
import { AcademicDeadlineScreen } from './features/deadlines/AcademicDeadlineScreen';
import { LessonSubjectScreen } from './features/subjects/LessonSubjectScreen';
import { SubjectScreen } from './features/subjects/SubjectScreen';
import { ProfileScreen } from './features/profile/ProfileScreen';
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
        { path: 'homework/:homeworkId', Component: HomeworkScreen },
        { path: 'homework/:homeworkId/edit', Component: HomeworkEditScreen },
        { path: 'tasks', Component: TasksScreen },
        { path: 'schedule', Component: ScheduleScreen },
        { path: 'schedule/lesson/:lessonId', Component: LessonScreen },
        { path: 'subjects', Component: SubjectsScreen },
        { path: 'subjects/lessons/:lessonId', Component: LessonSubjectScreen },
        { path: 'subjects/:subjectId', Component: SubjectScreen },
        { path: 'profile', Component: ProfileScreen },
        { path: 'profile/:section', Component: ProfileScreen },
        { path: 'deadlines', Component: DeadlinesScreen },
        { path: 'deadlines/:deadlineId', Component: AcademicDeadlineScreen },
        { path: 'deadlines/:deadlineId/edit', Component: EventEditScreen },
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
