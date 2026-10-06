/**
 * DEVELOPMENT-ONLY preview route (/preview/schedule). Static display strings from the approved
 * mockup, rendered through the production visual components. It is registered only when
 * `import.meta.env.DEV` is true, so production builds and the live `/schedule` route
 * («Раздел готовится») never show fake academic data.
 */
import { NextClassCard } from './NextClassCard';
import { ScheduleScreenView } from './ScheduleScreenView';
import type { NextClassView, ScheduleScreenView as View } from './viewModel';

const nextClass: NextClassView = {
  heading: 'Следующая пара',
  whenLabel: 'Через 1 час',
  timeRange: '10:30 — 12:00',
  subject: 'Математический анализ',
  details: 'Практика · Ауд. 320 · Иванов А. С.',
  badges: [
    { kind: 'confirmed', label: 'Подтверждено', tone: 'success' },
    { kind: 'group', label: 'Расписание группы', tone: 'neutral' },
  ],
  updated: 'Обновлено 09:12',
  onOpen: () => undefined,
};

const view: View = {
  scopes: { options: [{ key: 'personal', label: 'Моё расписание' }, { key: 'group', label: 'Расписание группы' }], active: 'group' },
  weekLabel: '1 неделя · нечётная',
  days: [
    {
      heading: 'Сегодня, 2 октября',
      items: [
        { id: '1', time: '08:30', title: 'История России', subtitle: 'Лекция · Ауд. 105', state: 'done', badges: [{ kind: 'confirmed', label: 'Подтверждено', tone: 'success' }], onOpen: () => undefined },
        { id: '2', time: '10:30', title: 'Математический анализ', subtitle: 'Практика · Ауд. 320', state: 'current', badges: [{ kind: 'moved', label: 'Перенесено', tone: 'info' }, { kind: 'now', label: 'Сейчас', tone: 'solid' }], onOpen: () => undefined },
        { id: '3', time: '12:30', title: 'Экономика', subtitle: 'Семинар · Ауд. 212', state: 'upcoming', badges: [{ kind: 'confirmed', label: 'Подтверждено', tone: 'success' }], onOpen: () => undefined },
        { id: '4', time: '14:30', title: 'Английский язык', subtitle: 'Практика · Ауд. 301', state: 'upcoming', badges: [], onOpen: () => undefined },
        { id: '5', time: '16:30', title: 'Физкультура', subtitle: 'Зал 2', state: 'upcoming', cancelled: true, badges: [{ kind: 'cancelled', label: 'Отменено', tone: 'danger' }], onOpen: () => undefined },
      ],
    },
  ],
};

export function SchedulePreview() {
  return (
    <div>
      <ScheduleScreenView view={view} />
      <div style={{ padding: '24px 20px 0' }}>
        <NextClassCard view={nextClass} />
      </div>
    </div>
  );
}
