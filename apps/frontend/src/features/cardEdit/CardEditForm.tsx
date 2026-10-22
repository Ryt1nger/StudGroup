import { useId, useState, type FormEvent } from 'react';
import type { LearningCardKind } from '@studgroup/shared-types';
import { isApiRequestError } from '../../api/errors';
import { ru } from '../../i18n/ru';
import { zonedParts, zonedToIso } from '../../lib/zoned';
import { Button } from '../../ui/Button';
import styles from './CardEditForm.module.css';

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
}

/** The fields that changed, in the wire shape of the PATCH body (without `expected_revision`). */
export interface CardEditPatch {
  title?: string;
  subject?: string;
  description?: string;
  kind?: Exclude<LearningCardKind, 'homework'>;
  date_hint?: string | null;
  deadline?: { at: string; date_only: boolean } | null;
}

interface Props {
  variant: 'homework' | 'event';
  initial: CardEditValues;
  timeZone: string;
  subjectSuggestions: string[];
  saving: boolean;
  error: unknown;
  onSave: (patch: CardEditPatch) => void;
  onCancel: () => void;
  /** When given, the page offers «Удалить карточку» with a confirmation step. */
  onDelete?: () => void;
  deleting?: boolean;
  deleteError?: unknown;
}

const KINDS: Exclude<LearningCardKind, 'homework'>[] = [
  'control_point',
  'assessment',
  'test',
  'credit',
  'exam',
  'other_academic_deadline',
];

function modeOf(deadline: CardEditValues['deadline']): DeadlineMode {
  if (!deadline.at) return 'none';
  return deadline.dateOnly ? 'date' : 'datetime';
}

/** Failed deletion -> a calm message in Russian. */
function deleteFailureMessage(error: unknown): string | null {
  if (error == null) return null;
  if (isApiRequestError(error)) {
    if (error.code === 'revision_conflict') return ru.edit.deleteErrors.conflict;
    if (error.code === 'permission_denied' || error.status === 403) return ru.edit.errors.forbidden;
    if (error.kind === 'network') return ru.edit.errors.network;
  }
  return ru.edit.deleteErrors.generic;
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

export function CardEditForm({
  variant,
  initial,
  timeZone,
  subjectSuggestions,
  saving,
  error,
  onSave,
  onCancel,
  onDelete,
  deleting = false,
  deleteError,
}: Props) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const ids = {
    title: useId(),
    subject: useId(),
    description: useId(),
    kind: useId(),
    hint: useId(),
    list: useId(),
  };
  const start = initial.deadline.at ? zonedParts(initial.deadline.at, timeZone) : { date: '', time: '' };
  const [title, setTitle] = useState(initial.title);
  const [subject, setSubject] = useState(initial.subject);
  const [description, setDescription] = useState(initial.description);
  const [kind, setKind] = useState(initial.kind);
  const [dateHint, setDateHint] = useState(initial.dateHint ?? '');
  const [mode, setMode] = useState<DeadlineMode>(modeOf(initial.deadline));
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(initial.deadline.dateOnly ? '' : start.time);
  const [problem, setProblem] = useState<string | null>(null);

  const failure = failureMessage(error);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return setProblem(ru.edit.errors.titleRequired);
    if (!subject.trim()) return setProblem(ru.edit.errors.subjectRequired);
    if (mode !== 'none' && !date) return setProblem(ru.edit.errors.dateRequired);
    if (mode === 'datetime' && !time) return setProblem(ru.edit.errors.timeRequired);
    setProblem(null);
    const patch: CardEditPatch = {};
    if (title.trim() !== initial.title) patch.title = title.trim();
    if (subject.trim() !== initial.subject) patch.subject = subject.trim();
    if (description.trim() !== initial.description.trim()) patch.description = description.trim();
    if (variant === 'event') {
      if (kind && kind !== initial.kind) patch.kind = kind;
      if (dateHint.trim() !== (initial.dateHint ?? '').trim()) patch.date_hint = dateHint.trim() || null;
    }
    const nextDeadline =
      mode === 'none'
        ? null
        : { at: zonedToIso(date, mode === 'date' ? '00:00' : time, timeZone), date_only: mode === 'date' };
    const before = initial.deadline.at
      ? { at: initial.deadline.at, date_only: initial.deadline.dateOnly }
      : null;
    const same =
      (before === null && nextDeadline === null) ||
      (before !== null &&
        nextDeadline !== null &&
        new Date(before.at).getTime() === new Date(nextDeadline.at).getTime() &&
        before.date_only === nextDeadline.date_only);
    if (!same) patch.deadline = nextDeadline;
    if (Object.keys(patch).length === 0) return onCancel();
    onSave(patch);
  }

  const message = problem ?? failure;

  return (
    <form className={styles.form} onSubmit={submit} aria-labelledby={`${ids.title}-heading`} noValidate>
      <h2 id={`${ids.title}-heading`} className={styles.heading}>
        {ru.edit.heading}
      </h2>

      {variant === 'event' ? (
        <div className={styles.field}>
          <label htmlFor={ids.kind}>{ru.edit.kind}</label>
          <select id={ids.kind} value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            {KINDS.map((value) => (
              <option key={value} value={value}>
                {ru.edit.kinds[value]}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <div className={styles.field}>
        <label htmlFor={ids.title}>{ru.edit.title}</label>
        <input
          id={ids.title}
          value={title}
          maxLength={255}
          autoComplete="off"
          aria-invalid={problem === ru.edit.errors.titleRequired || undefined}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      <div className={styles.field}>
        <label htmlFor={ids.subject}>{ru.edit.subject}</label>
        <input
          id={ids.subject}
          value={subject}
          maxLength={255}
          list={ids.list}
          autoComplete="off"
          aria-invalid={problem === ru.edit.errors.subjectRequired || undefined}
          onChange={(e) => setSubject(e.target.value)}
        />
        <datalist id={ids.list}>
          {subjectSuggestions.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </div>

      <div className={styles.field}>
        <label htmlFor={ids.description}>{ru.edit.description}</label>
        <textarea
          id={ids.description}
          value={description}
          rows={6}
          maxLength={8000}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>

      <fieldset className={styles.fieldset}>
        <legend>{ru.edit.deadline}</legend>
        <div className={styles.segments} role="radiogroup" aria-label={ru.edit.deadline}>
          {(
            [
              ['none', ru.edit.deadlineNone],
              ['date', ru.edit.deadlineDate],
              ['datetime', ru.edit.deadlineDateTime],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className={styles.segment} data-checked={mode === value || undefined}>
              <input
                type="radio"
                name={`${ids.title}-mode`}
                value={value}
                checked={mode === value}
                onChange={() => setMode(value)}
              />
              <span>{label}</span>
            </label>
          ))}
        </div>
        {mode !== 'none' ? (
          <div className={styles.row}>
            <div className={styles.field}>
              <label htmlFor={`${ids.title}-date`}>{ru.edit.date}</label>
              <input
                id={`${ids.title}-date`}
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </div>
            {mode === 'datetime' ? (
              <div className={styles.field}>
                <label htmlFor={`${ids.title}-time`}>{ru.edit.time}</label>
                <input
                  id={`${ids.title}-time`}
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </fieldset>

      {variant === 'event' ? (
        <div className={styles.field}>
          <label htmlFor={ids.hint}>{ru.edit.dateHint}</label>
          <input
            id={ids.hint}
            value={dateHint}
            maxLength={1000}
            placeholder={ru.edit.dateHintPlaceholder}
            autoComplete="off"
            onChange={(e) => setDateHint(e.target.value)}
          />
        </div>
      ) : null}

      {message ? (
        <p className={styles.error} role="alert">
          {message}
        </p>
      ) : null}

      <div className={styles.actions}>
        <Button type="submit" busy={saving}>
          {saving ? ru.edit.saving : ru.edit.save}
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving || deleting}>
          {ru.edit.cancel}
        </Button>
      </div>

      {onDelete ? (
        <div className={styles.danger}>
          {confirmingDelete ? (
            <div role="alertdialog" aria-labelledby={`${ids.title}-delete`} className={styles.confirm}>
              <p id={`${ids.title}-delete`} className={styles.confirmTitle}>
                {ru.edit.deleteTitle}
              </p>
              <p className={styles.confirmBody}>{ru.edit.deleteBody}</p>
              <div className={styles.actions}>
                <Button variant="dangerSolid" busy={deleting} onClick={onDelete}>
                  {deleting ? ru.edit.deleting : ru.edit.deleteConfirm}
                </Button>
                <Button
                  variant="secondary"
                  disabled={deleting}
                  onClick={() => setConfirmingDelete(false)}
                >
                  {ru.edit.deleteKeep}
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="danger" disabled={saving} onClick={() => setConfirmingDelete(true)}>
              {ru.edit.delete}
            </Button>
          )}
          {deleteFailureMessage(deleteError) ? (
            <p className={styles.error} role="alert">
              {deleteFailureMessage(deleteError)}
            </p>
          ) : null}
        </div>
      ) : null}
    </form>
  );
}
