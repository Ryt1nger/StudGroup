import type { AcademicDeadline, HomeworkSummary, LessonOccurrence, MaterialLink } from '@studgroup/shared-types';
import { Link } from 'react-router';
import { formatShortDate, formatTime } from '../../lib/format';
import { safeLessonUrl } from '../../lib/lessonLink';
import { subjectGlyph, subjectStyle } from '../../lib/subjectColor';
import { BackTop } from '../../shell/TopBar';
import { DlCard, HwCard, LessonRow } from '../shared/Cards';
import { MaterialList } from '../shared/Materials';
import { Sec } from '../shared/Sec';
import { useToggleDone } from '../shared/useToggleDone';

const uniq = (values: ReadonlyArray<string | null | undefined>) => [...new Set(values.filter((v): v is string => Boolean(v)))];

/** Everything about one subject: next lesson, facts, schedule, open homework, events and materials. */
export function SubjectView({
  name,
  timeZone,
  nowMs,
  lessons,
  homework,
  events,
  materials,
}: {
  name: string;
  timeZone: string;
  nowMs: number;
  lessons: readonly LessonOccurrence[];
  homework: readonly HomeworkSummary[];
  /** `null`: events are not connected for this view. */
  events: readonly AcademicDeadline[] | null;
  materials?: MaterialLink[];
}) {
  const { toggle, busy } = useToggleDone();
  const sorted = [...lessons].sort((a, b) => Date.parse(a.starts_at) - Date.parse(b.starts_at));
  const next = sorted.find((l) => Date.parse(l.ends_at) > nowMs && l.status !== 'cancelled');
  const teachers = uniq(sorted.map((l) => l.teacher));
  const places = uniq(sorted.map((l) => (safeLessonUrl(l.online_url) ? 'Онлайн' : l.location)));
  return (
    <>
      <BackTop fallback="/subjects" />
      <div className="d-head" style={subjectStyle(name)}>
        <span className="chipx"><b>{subjectGlyph(name)}</b>Предмет</span>
        <h1>{name}</h1>
      </div>
      {next ? (
        <Link className="hero" style={{ ...subjectStyle(name), marginTop: 16 }} to={`/schedule/lesson/${encodeURIComponent(next.id)}`} state={{ lesson: next, timeZone }}>
          <div className="eyebrow"><i />Ближайшая пара</div>
          <div className="hrow">
            <div className="time">{formatTime(next.starts_at, timeZone)}</div>
            <div className="hinfo">
              <b>{formatShortDate(next.starts_at, timeZone)}</b>
              {next.location ? <span>{next.location}</span> : null}
              {next.teacher ? <span>{next.teacher}</span> : null}
            </div>
          </div>
        </Link>
      ) : null}
      {teachers.length || places.length ? (
        <div className="block">
          <h3>О предмете</h3>
          {teachers.length ? <div className="kv"><span>{teachers.length > 1 ? 'Преподаватели' : 'Преподаватель'}</span><b>{teachers.join(', ')}</b></div> : null}
          {places.length ? <div className="kv"><span>Где проходят</span><b>{places.join(', ')}</b></div> : null}
        </div>
      ) : null}
      <Sec id="subject-schedule" title="Расписание">
        {sorted.length ? sorted.map((l) => <LessonRow key={l.id} lesson={l} timeZone={timeZone} nowMs={nowMs} showDate />) : <p className="muted">Пар по этому предмету пока нет.</p>}
      </Sec>
      <Sec id="subject-homework" title="Домашние задания">
        {homework.length ? (
          homework.map((item) => <HwCard key={item.id} item={item} nowMs={nowMs} timeZone={timeZone} busy={busy.has(item.id)} onToggle={(card) => void toggle(card)} />)
        ) : (
          <p className="muted">Активных заданий нет.</p>
        )}
      </Sec>
      <Sec id="subject-events" title="Ближайшие события и КТ">
        {events === null ? (
          <p className="muted">События и КТ пока не подключены.</p>
        ) : events.length ? (
          events.map((item) => <DlCard key={item.id} item={item} timeZone={timeZone} nowMs={nowMs} />)
        ) : (
          <p className="muted">Контрольных и тестов пока не назначено.</p>
        )}
      </Sec>
      <Sec id="subject-materials" title="Материалы">
        <div className="block" style={{ marginTop: 0 }}><MaterialList items={materials} /></div>
      </Sec>
    </>
  );
}
