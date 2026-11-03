import {
  changeStaffLesson,
  createStaffLesson,
  getStaffSchedule,
  type LessonChangeRequest,
  type LessonEdit,
  type LessonOccurrence,
} from '@studgroup/shared-types';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';
import { detailQueryOptions } from '../../api/cachePolicy';
import { unwrap } from '../../api/errors';
import { addDays, dayKey, formatDayKey, formatTime, weekRangeForDayKey } from '../../lib/format';
import { useIsEditor, useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton, StateView } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';
import { RequestError } from '../../ui/RequestError';

const DAYS = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

/** Only issue local wall-clock times with an explicit offset, never reinterpret them in the device TZ. */
function localScheduleTime(day: string, clock: string, timeZone: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !/^\d{2}:\d{2}$/.test(clock)) {
    throw new Error('Неверная дата или время');
  }
  const atNoon = new Date(`${day}T12:00:00Z`);
  const zone = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'shortOffset' })
    .formatToParts(atNoon).find((part) => part.type === 'timeZoneName')?.value;
  const parsed = /^GMT([+-])(\d{1,2})(?::(\d{2}))?$/.exec(zone ?? '');
  if (zone !== 'GMT' && zone !== 'UTC' && !parsed) {
    throw new Error('Не удалось определить часовой пояс группы');
  }
  const offset = parsed
    ? `${parsed[1]}${parsed[2]!.padStart(2, '0')}:${parsed[3] ?? '00'}`
    : '+00:00';
  return `${day}T${clock}:00${offset}`;
}

function validateTimeRange(start: string, end: string): string | null {
  if (!/^\d{2}:\d{2}$/.test(start) || !/^\d{2}:\d{2}$/.test(end) || end <= start) {
    return 'Конец пары должен быть позже начала в тот же день';
  }
  return null;
}

function LessonEditor({
  lesson,
  revision,
  timeZone,
  onDone,
}: {
  lesson: LessonOccurrence;
  revision: LessonEdit;
  timeZone: string;
  onDone: () => void;
}) {
  const queries = useQueryClient();
  const [subject, setSubject] = useState(lesson.subject);
  const [teacher, setTeacher] = useState(lesson.teacher ?? '');
  const [location, setLocation] = useState(lesson.location ?? '');
  const [starts, setStarts] = useState(formatTime(lesson.starts_at, timeZone));
  const [ends, setEnds] = useState(formatTime(lesson.ends_at, timeZone));
  const [cancelled, setCancelled] = useState(lesson.status === 'cancelled');
  const [scope, setScope] = useState<'once' | 'recurring'>('once');
  const [problem, setProblem] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: LessonChangeRequest) =>
      unwrap(changeStaffLesson({ path: { lesson_id: lesson.id }, body })),
    onSuccess: async () => {
      await queries.invalidateQueries();
      onDone();
    },
  });
  function save() {
    const name = subject.trim();
    if (!name) return setProblem('Укажите название предмета');
    const timeError = validateTimeRange(starts, ends);
    if (timeError) return setProblem(timeError);
    const body: LessonChangeRequest = {
      scope,
      pattern_revision: revision.pattern_revision,
      override_revision: revision.override_revision,
    };
    if (name !== lesson.subject) body.subject = name;
    if (teacher.trim() !== (lesson.teacher ?? '')) body.teacher = teacher.trim() || null;
    if (location.trim() !== (lesson.location ?? '')) body.location = location.trim() || null;
    if (starts !== formatTime(lesson.starts_at, timeZone) ||
        ends !== formatTime(lesson.ends_at, timeZone)) {
      const date = dayKey(lesson.starts_at, timeZone);
      try {
        body.starts_at = localScheduleTime(date, starts, timeZone);
        body.ends_at = localScheduleTime(date, ends, timeZone);
      } catch (error) {
        return setProblem(error instanceof Error ? error.message : 'Неверное время');
      }
    }
    if (cancelled !== (lesson.status === 'cancelled')) body.cancelled = cancelled;
    if (Object.keys(body).length === 3) return setProblem('Измените хотя бы одно поле');
    setProblem(null);
    mutation.mutate(body);
  }
  return (
    <form className="manage-form" aria-label={`Редактирование: ${lesson.subject}`} onSubmit={(event) => {
      event.preventDefault();
      save();
    }}>
      <div className="field"><label>Предмет
        <input value={subject} maxLength={255} required onChange={(event) => setSubject(event.target.value)} />
      </label></div>
      <div className="field"><label>Преподаватель
        <input value={teacher} maxLength={255} onChange={(event) => setTeacher(event.target.value)} />
      </label></div>
      <div className="field"><label>Начало
        <input type="time" value={starts} required onChange={(event) => setStarts(event.target.value)} />
      </label></div>
      <div className="field"><label>Конец
        <input type="time" value={ends} required onChange={(event) => setEnds(event.target.value)} />
      </label></div>
      <div className="field"><label>Аудитория или место
        <input value={location} maxLength={512} onChange={(event) => setLocation(event.target.value)} />
      </label></div>
      <div className="field"><label>Область изменения
        <select value={scope} disabled={revision.single_day} onChange={(event) =>
          setScope(event.target.value as 'once' | 'recurring')
        }>
          <option value="once">Только эта дата</option>
          <option value="recurring">Все недели этого правила</option>
        </select>
      </label></div>
      <label className="manage-check">
        <input type="checkbox" checked={cancelled} onChange={(event) => setCancelled(event.target.checked)} />
        Отменить эту пару
      </label>
      <p className="manage-subtle">
        {scope === 'once'
          ? 'Разовое изменение не затронет остальные недели.'
          : 'Регулярное изменение применяется ко всему действующему правилу расписания.'}
      </p>
      {problem ? <p role="alert">{problem}</p> : null}
      {mutation.isError ? <RequestError error={mutation.error} /> : null}
      <div className="manage-form-actions">
        <button className="btn" type="button" disabled={mutation.isPending} onClick={onDone}>Отмена</button>
        <button className="btn on" type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Сохранение…' : 'Сохранить'}
        </button>
      </div>
    </form>
  );
}

function AddLesson({
  start,
  end,
  timeZone,
  onDone,
}: {
  start: string;
  end: string;
  timeZone: string;
  onDone: () => void;
}) {
  const queries = useQueryClient();
  const [day, setDay] = useState(start);
  const [subject, setSubject] = useState('');
  const [starts, setStarts] = useState('09:00');
  const [ends, setEnds] = useState('10:20');
  const [location, setLocation] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (body: { subject: string; starts_at: string; ends_at: string; location?: string | null }) =>
      unwrap(createStaffLesson({ body })),
    onSuccess: async () => {
      await queries.invalidateQueries();
      onDone();
    },
  });
  function save() {
    if (!subject.trim()) return setProblem('Укажите предмет');
    if (day < start || day > end) return setProblem('Выберите дату этой недели');
    const timeError = validateTimeRange(starts, ends);
    if (timeError) return setProblem(timeError);
    try {
      mutation.mutate({
        subject: subject.trim(),
        starts_at: localScheduleTime(day, starts, timeZone),
        ends_at: localScheduleTime(day, ends, timeZone),
        location: location.trim() || null,
      });
      setProblem(null);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : 'Неверное время');
    }
  }
  return (
    <form className="manage-form" aria-label="Добавить пару" onSubmit={(event) => {
      event.preventDefault();
      save();
    }}>
      <strong>Новая разовая пара</strong>
      <p className="manage-subtle">Для постоянной замены целой недели используйте загрузку скриншота.</p>
      <div className="field"><label>Дата
        <input type="date" required min={start} max={end} value={day} onChange={(event) => setDay(event.target.value)} />
      </label></div>
      <div className="field"><label>Предмет
        <input required maxLength={255} value={subject} onChange={(event) => setSubject(event.target.value)} />
      </label></div>
      <div className="field"><label>Начало
        <input type="time" required value={starts} onChange={(event) => setStarts(event.target.value)} />
      </label></div>
      <div className="field"><label>Конец
        <input type="time" required value={ends} onChange={(event) => setEnds(event.target.value)} />
      </label></div>
      <div className="field"><label>Аудитория
        <input maxLength={512} value={location} onChange={(event) => setLocation(event.target.value)} />
      </label></div>
      {problem ? <p role="alert">{problem}</p> : null}
      {mutation.isError ? <RequestError error={mutation.error} /> : null}
      <div className="manage-form-actions">
        <button type="button" className="btn" onClick={onDone} disabled={mutation.isPending}>Отмена</button>
        <button type="submit" className="btn on" disabled={mutation.isPending}>
          {mutation.isPending ? 'Сохранение…' : 'Добавить'}
        </button>
      </div>
    </form>
  );
}

/** Staff-only overview. Every write uses the existing versioned/audited staff API. */
export function ScheduleManageScreen() {
  const editor = useIsEditor();
  const groupTimeZone = useGroupTimeZone();
  const now = useSessionServerNow();
  const [weekStart, setWeekStart] = useState(() =>
    weekRangeForDayKey(dayKey(now() ?? Date.now(), groupTimeZone)).start,
  );
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const end = addDays(weekStart, 6);
  const query = useQuery({
    ...detailQueryOptions,
    queryKey: ['staff-schedule-manage', weekStart, end],
    queryFn: () => unwrap(getStaffSchedule({ query: { start: weekStart, end } })),
    enabled: editor,
  });
  const schedule = query.data?.schedule;
  const timeZone = schedule?.group_timezone ?? groupTimeZone;
  const revisions = new Map(query.data?.lesson_edits.map((row) => [row.lesson_id, row]) ?? []);
  function moveWeek(delta: number) {
    setEditing(null);
    setAdding(false);
    setWeekStart((value) => addDays(value, delta * 7));
  }

  return (
    <>
      <BackTop fallback="/schedule" />
      <h1 className="ptitle">Управление расписанием</h1>
      {!editor ? (
        <StateView icon="lock" title="Нет доступа" body="Изменять расписание могут староста и заместитель." />
      ) : (
        <>
          <p className="manage-intro">
            Просматривайте пары, редактируйте действующее расписание или загружайте скриншот.
            Все изменения сохраняются только после вашего подтверждения.
          </p>
          <div className="manage-quick">
            <Link to="/schedule/review" aria-label="Загрузить расписание и открыть черновики">
              <Ic name="cal" size={21} />
              <span><strong>Загрузить скриншот</strong><small>DeepSeek · черновики</small></span>
              <Ic name="chev" size={17} />
            </Link>
          </div>
          <div className="block">
            <h2>Действующее расписание</h2>
            <div className="manage-toolbar">
              <button type="button" aria-label="Предыдущая неделя" onClick={() => moveWeek(-1)}>
                <Ic name="chev" size={19} className="manage-chevron-back" />
              </button>
              <span>{weekStart.slice(8, 10)}.{weekStart.slice(5, 7)} — {end.slice(8, 10)}.{end.slice(5, 7)}</span>
              <button type="button" aria-label="Следующая неделя" onClick={() => moveWeek(1)}>
                <Ic name="chev" size={19} />
              </button>
            </div>
            {schedule?.selected_week ? (
              <p className="manage-subtle">
                {schedule.selected_week === 'first' ? 'Первая' : 'Вторая'} учебная неделя · {schedule.group_timezone}
              </p>
            ) : (
              <p className="manage-subtle">Чётность недели пока не определена.</p>
            )}
            <button type="button" className="pillb" onClick={() => {
              setEditing(null);
              setAdding((value) => !value);
            }}>
              <Ic name="plus" size={16} />
              Добавить пару
            </button>
            {adding ? (
              <AddLesson key={weekStart} start={weekStart} end={end} timeZone={timeZone}
                onDone={() => setAdding(false)} />
            ) : null}
            {query.isPending ? <Skeleton label="Загрузка расписания" count={3} /> : null}
            {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
            {schedule ? DAYS.map((name, index) => {
              const date = addDays(weekStart, index);
              const lessons = schedule.lessons
                .filter((lesson) => dayKey(lesson.starts_at, timeZone) === date)
                .sort((a, b) => a.starts_at.localeCompare(b.starts_at));
              return (
                <section className="manage-day" key={date}>
                  <h3>{name} · {formatDayKey(date).split(', ').slice(1).join(', ')}</h3>
                  {!lessons.length ? <span className="manage-empty-day">Нет пар</span> : null}
                  {lessons.map((lesson) => {
                    const revision = revisions.get(lesson.id);
                    return (
                      <div key={lesson.id}>
                        <div className="manage-lesson">
                          <div className="manage-lesson-body">
                            <strong>{lesson.subject}</strong>
                            <span>
                              {formatTime(lesson.starts_at, timeZone)}–{formatTime(lesson.ends_at, timeZone)}
                              {lesson.location ? ` · ${lesson.location}` : ''}
                              {lesson.teacher ? ` · ${lesson.teacher}` : ''}
                              {lesson.status === 'cancelled' ? ' · Отменена' : ''}
                            </span>
                          </div>
                          {revision ? (
                            <button type="button" aria-label={`Изменить пару: ${lesson.subject}`}
                              aria-expanded={editing === lesson.id} onClick={() => {
                                setAdding(false);
                                setEditing((value) => value === lesson.id ? null : lesson.id);
                              }}>
                              <Ic name="edit" size={17} />
                            </button>
                          ) : null}
                        </div>
                        {revision && editing === lesson.id ? (
                          <LessonEditor
                            key={`${lesson.id}:${revision.pattern_revision}:${revision.override_revision}`}
                            lesson={lesson}
                            revision={revision}
                            timeZone={timeZone}
                            onDone={() => setEditing(null)}
                          />
                        ) : null}
                      </div>
                    );
                  })}
                </section>
              );
            }) : null}
          </div>
          {!query.isPending && query.data && !query.data.schedule.lessons.length ? (
            <Empty title="В эту неделю пар нет" body="Добавьте разовую пару или загрузите расписание по скриншоту." />
          ) : null}
          <Banner tone="info" icon="info">
            При изменении постоянного правила затрагиваются другие недели той же чётности.
            Перед сохранением проверяйте область изменения.
          </Banner>
        </>
      )}
    </>
  );
}
