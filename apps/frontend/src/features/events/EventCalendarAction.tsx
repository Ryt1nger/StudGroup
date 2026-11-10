import type { PublishedEvent } from '@studgroup/shared-types';
import { useEffect, useId, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { isApiRequestError } from '../../api/errors';
import { zonedToIso } from '../../lib/zoned';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { useFeedback } from '../../ui/Feedback';
import { RequestError } from '../../ui/RequestError';
import { useEventCapabilities, useReviewEvent } from './api';
import { eventDays } from './calendar';
import { eventPayload } from './eventPayload';

/**
 * Dated published events are already in the group calendar. For an undated event,
 * staff can supply the date through the existing version-checked editor API.
 * No local-only "saved" state, duplicate academic deadline or fictitious write.
 */
export function EventCalendarAction({ item }: { item: PublishedEvent }) {
  const timeZone = useGroupTimeZone();
  const features = useEventCapabilities();
  const calendar = eventDays(item, timeZone);
  const mutation = useReviewEvent(item.id);
  const { toast } = useFeedback();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const titleId = useId();
  const canEdit = features.event_review === true;
  const conflict = isApiRequestError(mutation.error) && mutation.error.status === 409;

  useEffect(() => {
    if (!open) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !mutation.isPending) setOpen(false);
    };
    document.addEventListener('keydown', keydown);
    return () => document.removeEventListener('keydown', keydown);
  }, [open, mutation.isPending]);

  if (item.state !== 'active') return null;
  if (calendar) {
    return (
      <Link className="pillb event-calendar-action" to={`/schedule?day=${calendar.first}`}>
        В расписании · Открыть
      </Link>
    );
  }

  async function saveDate() {
    if (!date) {
      setProblem('Укажите дату события');
      return;
    }
    if (!canEdit) return;
    setProblem(null);
    try {
      await mutation.mutateAsync({
        expected_revision: item.revision,
        state: 'active',
        payload: {
          ...eventPayload(item),
          starts_at: time ? zonedToIso(date, time, timeZone) : null,
          starts_on: time ? null : date,
          ends_at: null,
          ends_on: null,
          multi_day: false,
          when_text: null,
        },
        accept_source_ids: [],
      });
      toast('Дата сохранена. Событие теперь в расписании.');
      setOpen(false);
      void navigate(`/schedule?day=${date}`);
    } catch {
      // The mutation exposes a localized request error and refreshes stale revisions.
    }
  }

  return (
    <>
      <button type="button" className="pillb event-calendar-action" onClick={() => setOpen(true)}>
        Добавить в расписание
      </button>
      {open ? (
        <div
          className="modal-back"
          onClick={(event) => event.target === event.currentTarget && !mutation.isPending && setOpen(false)}
        >
          <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="modal event-date-modal">
            <h3 id={titleId}>Добавить дату события</h3>
            <p>
              Для появления в расписании нужна дата события. Введённая дата будет видна участникам
              группы и появится в календаре автоматически.
            </p>
            {canEdit ? (
              <form
                className="form"
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveDate();
                }}
              >
                <div className="field">
                  <label htmlFor={`${titleId}-date`}>Дата события (дедлайн)</label>
                  <input
                    id={`${titleId}-date`}
                    type="date"
                    value={date}
                    required
                    autoFocus
                    onChange={(event) => setDate(event.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor={`${titleId}-time`}>Время, если известно</label>
                  <input
                    id={`${titleId}-time`}
                    type="time"
                    value={time}
                    onChange={(event) => setTime(event.target.value)}
                  />
                </div>
                <p className="dim">Часовой пояс группы: {timeZone}</p>
                {problem ? <p className="ferr" role="alert">{problem}</p> : null}
                {conflict ? (
                  <p role="alert">Событие уже изменилось. Закройте окно и откройте актуальную карточку.</p>
                ) : null}
                {mutation.isError && !conflict ? <RequestError error={mutation.error} /> : null}
                <div className="mbtns">
                  <button type="button" disabled={mutation.isPending} onClick={() => setOpen(false)}>
                    Отмена
                  </button>
                  <button type="submit" className="btn on" disabled={mutation.isPending || conflict}>
                    {mutation.isPending ? 'Сохраняем…' : 'Сохранить дату'}
                  </button>
                </div>
              </form>
            ) : (
              <>
                <p className="dim">Уточнить дату общего события может староста или заместитель.</p>
                <div className="mbtns">
                  <button type="button" onClick={() => setOpen(false)}>Понятно</button>
                </div>
              </>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}
