import clsx from 'clsx';
import { useId, useState } from 'react';
import type { LearningCardKind } from '@studgroup/shared-types';
import { isApiRequestError } from '../../api/errors';
import { ru } from '../../i18n/ru';
import { zonedParts, zonedToIso } from '../../lib/zoned';
import { Float } from '../../shell/AppRoot';
import { Spinner } from '../../ui/Blocks';
import { Ic } from '../../ui/Ic';

type DeadlineMode = 'none' | 'date' | 'datetime';

export interface CardEditValues {
  title: string;
  subject: string;
  description: string;
  /** Only academic events have a kind; homework leaves it undefined. */
  kind?: Exclude<LearningCardKind, 'homework'>;
  /** Only academic events have a free-text hint about the date. */
  dateHint?: string;
  deadline: { at: string | null; dateOnly: boolean };
  /** Homework only: the red «Срочно» mark. */
  urgent?: boolean;
}

/** The fields that changed, in the wire shape of the PATCH body (without `expected_revision`). */
export interface CardEditPatch {
  title?: string;
  urgency?: 'normal' | 'urgent';
  subject?: string;
  description?: string;
  kind?: Exclude<LearningCardKind, 'homework'>;
  date_hint?: string | null;
  deadline?: { at: string; date_only: boolean } | null;
}

interface Props {
  variant: 'homework' | 'event';
  /** `create`: a new card, every filled field is sent. */
  purpose?: 'edit' | 'create';
  initial: CardEditValues;
  timeZone: string;
  subjectSuggestions: string[];
  saving: boolean;
  error: unknown;
  onSave: (patch: CardEditPatch) => void;
  onCancel: () => void;
}

const KINDS: Exclude<LearningCardKind, 'homework'>[] = ['control_point', 'assessment', 'test', 'credit', 'exam', 'other_academic_deadline'];

function modeOf(deadline: CardEditValues['deadline']): DeadlineMode {
  if (!deadline.at) return 'none';
  return deadline.dateOnly ? 'date' : 'datetime';
}

/** Server error -> a calm message in Russian; never shows wire codes. */
function failureMessage(error: unknown): string | null {
  if (error == null) return null;
  if (isApiRequestError(error)) {
    if (error.code === 'revision_conflict') return ru.edit.errors.conflict;
    if (error.code === 'permission_denied' || error.status === 403) return ru.edit.errors.forbidden;
    if (error.status === 422) return ru.edit.errors.invalid;
    if (error.kind === 'network') return ru.edit.errors.network;
  }
  return ru.edit.errors.generic;
}

const MODES: ReadonlyArray<readonly [DeadlineMode, string]> = [
  ['none', ru.edit.deadlineNone],
  ['date', ru.edit.deadlineDate],
  ['datetime', ru.edit.deadlineDateTime],
];

export function CardEditForm({ variant, purpose = 'edit', initial, timeZone, subjectSuggestions, saving, error, onSave, onCancel }: Props) {
  const ids = { title: useId(), subject: useId(), description: useId(), kind: useId(), hint: useId(), list: useId(), mode: useId() };
  const start = initial.deadline.at ? zonedParts(initial.deadline.at, timeZone) : { date: '', time: '' };
  const [title, setTitle] = useState(initial.title);
  const [subject, setSubject] = useState(initial.subject);
  const [description, setDescription] = useState(initial.description);
  const [kind, setKind] = useState(initial.kind);
  const [dateHint, setDateHint] = useState(initial.dateHint ?? '');
  const [mode, setMode] = useState<DeadlineMode>(modeOf(initial.deadline));
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(initial.deadline.dateOnly ? '' : start.time);
  const [urgent, setUrgent] = useState(initial.urgent ?? false);
  const [problem, setProblem] = useState<string | null>(null);
  const message = problem ?? failureMessage(error);

  function submit() {
    if (!title.trim()) return setProblem(ru.edit.errors.titleRequired);
    if (!subject.trim()) return setProblem(ru.edit.errors.subjectRequired);
    if (mode !== 'none' && !date) return setProblem(ru.edit.errors.dateRequired);
    if (mode === 'datetime' && !time) return setProblem(ru.edit.errors.timeRequired);
    setProblem(null);
    const patch: CardEditPatch = {};
    if (title.trim() !== initial.title) patch.title = title.trim();
    if (subject.trim() !== initial.subject) patch.subject = subject.trim();
    if (description.trim() !== initial.description.trim()) patch.description = description.trim();
    if (variant === 'homework' && urgent !== (initial.urgent ?? false)) patch.urgency = urgent ? 'urgent' : 'normal';
    if (variant === 'event') {
      if (kind && kind !== initial.kind) patch.kind = kind;
      if (dateHint.trim() !== (initial.dateHint ?? '').trim()) patch.date_hint = dateHint.trim() || null;
    }
    const nextDeadline = mode === 'none' ? null : { at: zonedToIso(date, mode === 'date' ? '00:00' : time, timeZone), date_only: mode === 'date' };
    const before = initial.deadline.at ? { at: initial.deadline.at, date_only: initial.deadline.dateOnly } : null;
    const same =
      (before === null && nextDeadline === null) ||
      (before !== null && nextDeadline !== null && new Date(before.at).getTime() === new Date(nextDeadline.at).getTime() && before.date_only === nextDeadline.date_only);
    if (!same) patch.deadline = nextDeadline;
    if (purpose === 'create') {
      // A new card has no earlier values: everything that is filled goes to the server.
      patch.title = title.trim();
      patch.subject = subject.trim();
      if (description.trim()) patch.description = description.trim();
      if (variant === 'event') patch.kind = kind ?? 'assessment';
    } else if (Object.keys(patch).length === 0) return onCancel();
    onSave(patch);
  }

  return (
    <>
      <h1 className="ptitle">
        {purpose === 'create' ? ru.edit.create.pageTitle : ru.edit.pageTitle}
        <small>{variant === 'homework' ? 'Задание' : 'Событие'}</small>
      </h1>
      <form className="form" noValidate aria-label={ru.edit.heading} onSubmit={(event) => { event.preventDefault(); submit(); }}>
        {variant === 'event' ? (
          <div className="field">
            <label htmlFor={ids.kind}>{ru.edit.kind}</label>
            <select id={ids.kind} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
              {KINDS.map((value) => <option key={value} value={value}>{ru.edit.kinds[value]}</option>)}
            </select>
          </div>
        ) : null}
        <div className="field">
          <label htmlFor={ids.title}>{ru.edit.title}</label>
          <input id={ids.title} type="text" value={title} maxLength={255} autoComplete="off" aria-invalid={problem === ru.edit.errors.titleRequired || undefined} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={ids.subject}>{ru.edit.subject}</label>
          <input id={ids.subject} type="text" value={subject} maxLength={255} list={ids.list} autoComplete="off" aria-invalid={problem === ru.edit.errors.subjectRequired || undefined} onChange={(e) => setSubject(e.target.value)} />
          <datalist id={ids.list}>{subjectSuggestions.map((name) => <option key={name} value={name} />)}</datalist>
        </div>
        <div className="field">
          <label htmlFor={ids.description}>{ru.edit.description}</label>
          <textarea id={ids.description} rows={5} maxLength={8000} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="field">
          <label id={`${ids.mode}-l`}>{ru.edit.deadline}</label>
          <div className="segc" role="radiogroup" aria-labelledby={`${ids.mode}-l`}>
            {MODES.map(([value, label]) => (
              <button key={value} type="button" role="radio" aria-checked={mode === value} aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>
            ))}
          </div>
          {mode !== 'none' ? (
            <div className="dates" style={{ padding: '12px 0 0' }}>
              <div>
                <label htmlFor={`${ids.mode}-date`} className="sr">{ru.edit.date}</label>
                <input id={`${ids.mode}-date`} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
              </div>
              {mode === 'datetime' ? (
                <div>
                  <label htmlFor={`${ids.mode}-time`} className="sr">{ru.edit.time}</label>
                  <input id={`${ids.mode}-time`} type="time" value={time} onChange={(e) => setTime(e.target.value)} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        {variant === 'homework' ? (
          <div className="grp" style={{ marginTop: 16 }}>
            <div className="srow">
              <span className="ri"><Ic name="bolt" size={18} /></span>
              <div><div className="st">Срочно</div><div className="ss">Красная отметка у карточки в списках</div></div>
              <button type="button" className="sw" role="switch" aria-checked={urgent} aria-label="Срочно" onClick={() => setUrgent((v) => !v)} />
            </div>
          </div>
        ) : null}
        {variant === 'event' ? (
          <div className="field">
            <label htmlFor={ids.hint}>{ru.edit.dateHint}</label>
            <input id={ids.hint} type="text" value={dateHint} maxLength={1000} placeholder={ru.edit.dateHintPlaceholder} autoComplete="off" onChange={(e) => setDateHint(e.target.value)} />
          </div>
        ) : null}
        {message ? <p className="ferr" role="alert">{message}</p> : null}
      </form>
      <div className="cta-space" />
      <Float>
        <div className="cta-bar">
          <button type="button" className={clsx('btn', saving && 'busy')} disabled={saving} onClick={submit}>
            {saving ? <><Spinner />{purpose === 'create' ? ru.edit.create.submitting : ru.edit.saving}</> : purpose === 'create' ? ru.edit.create.submit : ru.edit.save}
          </button>
        </div>
      </Float>
    </>
  );
}
