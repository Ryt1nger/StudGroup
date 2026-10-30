import type { EventPayload, EventReview } from '@studgroup/shared-types';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { isApiRequestError } from '../../api/errors';
import { zonedParts, zonedToIso } from '../../lib/zoned';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop } from '../../shell/TopBar';
import { Banner, ConfirmModal, Skeleton, StateView } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { EventSource, EventStatus } from './EventCard';
import { useEventCapabilities, useEventReview, useReviewEvent } from './api';
import { eventDate } from './calendar';

function payloadOf(item: EventPayload): EventPayload {
  return {
    title: item.title,
    description: item.description ?? '',
    starts_at: item.starts_at ?? null,
    ends_at: item.ends_at ?? null,
    starts_on: item.starts_on ?? null,
    ends_on: item.ends_on ?? null,
    multi_day: item.multi_day ?? false,
    when_text: item.when_text ?? null,
    place: item.place ?? null,
    conditions: item.conditions ?? null,
    audience: item.audience ?? 'unknown',
    audience_label: item.audience_label ?? null,
  };
}

export function EventReviewDetailScreen() {
  const { eventId = '' } = useParams();
  const features = useEventCapabilities();
  const query = useEventReview();
  const item = query.data?.events.find((event) => event.id === eventId);
  const [reload, setReload] = useState(0);
  return (
    <>
      <BackTop fallback="/events/review" />
      <h1 className="ptitle">Проверка события</h1>
      {!features.event_review ? (
        <StateView
          icon="lock"
          title="Нет доступа"
          body="Проверка доступна уполномоченному редактору группы."
        />
      ) : (
        <>
          {query.isPending ? <Skeleton label="Загрузка события" /> : null}
          {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
          {query.data && !item ? (
            <StateView
              icon="alert"
              title="Событие недоступно"
              body="Источник или срок хранения события могли истечь."
            />
          ) : null}
          {item ? (
            <ReviewForm
              key={`${item.id}:${reload}`}
              item={item}
              reload={async () => {
                await query.refetch();
                setReload((value) => value + 1);
              }}
            />
          ) : null}
        </>
      )}
    </>
  );
}

function ReviewForm({ item, reload }: { item: EventReview; reload: () => Promise<void> }) {
  // A background refresh must neither erase editor input nor silently adopt a new revision.
  const [initial] = useState(item);
  const timeZone = useGroupTimeZone();
  const navigate = useNavigate();
  const features = useEventCapabilities();
  const mutation = useReviewEvent(initial.id);
  const start = initial.starts_at
    ? zonedParts(initial.starts_at, timeZone)
    : { date: initial.starts_on ?? '', time: '' };
  const end = initial.ends_at
    ? zonedParts(initial.ends_at, timeZone)
    : { date: initial.ends_on ?? '', time: '' };
  const [title, setTitle] = useState(initial.title);
  const [description, setDescription] = useState(initial.description ?? '');
  const [date, setDate] = useState(start.date);
  const [time, setTime] = useState(start.time);
  const [endDate, setEndDate] = useState(end.date);
  const [endTime, setEndTime] = useState(end.time);
  const [multiDay, setMultiDay] = useState(initial.multi_day ?? false);
  const [place, setPlace] = useState(initial.place ?? '');
  const [conditions, setConditions] = useState(initial.conditions ?? '');
  const [audience, setAudience] = useState(initial.audience ?? 'unknown');
  const [label, setLabel] = useState(initial.audience_label ?? '');
  const [accepted, setAccepted] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [cancel, setCancel] = useState(false);
  const conflict = isApiRequestError(mutation.error) && mutation.error.status === 409;

  function save(state: 'draft' | 'active' | 'cancelled') {
    if (!title.trim()) return setProblem('Укажите название события');
    if (time && !date) return setProblem('Укажите дату начала');
    if (endTime && !endDate && !date) return setProblem('Укажите дату окончания');
    if (endDate && date && endDate < date) return setProblem('Окончание раньше начала');
    if (state === 'active' && (!date || audience !== 'group'))
      return setProblem('Перед публикацией подтвердите дату и аудиторию группы');
    setProblem(null);
    const payload: EventPayload =
      state === 'cancelled'
        ? payloadOf(initial)
        : {
            title: title.trim(),
            description,
            place: place.trim() || null,
            conditions: conditions.trim() || null,
            audience,
            audience_label: label.trim() || null,
            multi_day: multiDay,
            starts_at: date && time ? zonedToIso(date, time, timeZone) : null,
            starts_on: date && !time ? date : null,
            ends_at: endTime ? zonedToIso(endDate || date, endTime, timeZone) : null,
            ends_on: endDate && !endTime ? endDate : null,
            when_text: date ? null : (initial.when_text ?? null),
          };
    mutation.mutate(
      { expected_revision: initial.revision, payload, state, accept_source_ids: accepted },
      {
        onSuccess: () => {
          void navigate(features.events && state !== 'draft' ? `/events/${initial.id}` : '/events/review', {
            replace: true,
          });
        },
      },
    );
  }

  return (
    <>
      <EventStatus item={initial} />
      <p>{eventDate(initial, timeZone)}</p>
      <EventSource url={initial.source_url} />
      {!initial.source_url ? (
        <Banner tone="warning" icon="alert">
          Источник недоступен. Новый черновик нельзя опубликовать без актуального подтверждения источника.
        </Banner>
      ) : null}
      <form
        className="form"
        aria-label="Редактирование события"
        onSubmit={(event) => {
          event.preventDefault();
          if (initial.state !== 'cancelled') save(initial.state === 'active' ? 'active' : 'draft');
        }}
      >
        <fieldset disabled={mutation.isPending} className="event-fields">
          <div className="field">
            <label>
              Название
              <input
                value={title}
                maxLength={255}
                required
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>
          </div>
          <div className="field">
            <label>
              Описание
              <textarea
                value={description}
                maxLength={8000}
                rows={5}
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
          </div>
          <p className="dim">Дата и время по часовому поясу группы: {timeZone}</p>
          <div className="field">
            <label>
              Дата начала
              <input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
            </label>
          </div>
          <div className="field">
            <label>
              Время начала, если известно
              <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
            </label>
          </div>
          <div className="field">
            <label>
              Дата окончания
              <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
            </label>
          </div>
          <div className="field">
            <label>
              Время окончания, если известно
              <input type="time" value={endTime} onChange={(event) => setEndTime(event.target.value)} />
            </label>
          </div>
          <label>
            <input
              type="checkbox"
              checked={multiDay}
              onChange={(event) => setMultiDay(event.target.checked)}
            />{' '}
            Несколько дней
          </label>
          <div className="field">
            <label>
              Место
              <input value={place} maxLength={500} onChange={(event) => setPlace(event.target.value)} />
            </label>
          </div>
          <div className="field">
            <label>
              Условия участия
              <textarea
                value={conditions}
                rows={3}
                maxLength={2000}
                onChange={(event) => setConditions(event.target.value)}
              />
            </label>
          </div>
          <div className="field">
            <label>
              Аудитория
              <select
                value={audience}
                onChange={(event) => setAudience(event.target.value as typeof audience)}
              >
                <option value="unknown">Не подтверждена</option>
                <option value="group">Вся группа</option>
                <option value="subgroup">Подгруппа</option>
                <option value="personal">Личная</option>
              </select>
            </label>
          </div>
          <div className="field">
            <label>
              Уточнение аудитории
              <input value={label} maxLength={255} onChange={(event) => setLabel(event.target.value)} />
            </label>
          </div>
          {initial.findings
            .filter((finding) => finding.state === 'held')
            .map((finding) => (
              <div key={finding.id} className="event-review-source">
                <b>Распознанное уточнение</b>
                <p>{finding.payload.title}</p>
                <p className="event-description">{finding.payload.description}</p>
                <p>
                  {eventDate(finding.payload, timeZone)} · {finding.payload.place}
                </p>
                <EventSource url={finding.source_url} />
                <label>
                  <input
                    type="checkbox"
                    checked={accepted.includes(finding.id)}
                    onChange={(event) =>
                      setAccepted((ids) =>
                        event.target.checked ? [...ids, finding.id] : ids.filter((id) => id !== finding.id),
                      )
                    }
                  />
                  Источник проверен, изменения внесены в форму
                </label>
              </div>
            ))}
          {problem ? <p role="alert">{problem}</p> : null}
          {conflict ? (
            <Banner
              tone="warning"
              icon="alert"
              action={
                <button type="button" className="bact txt" onClick={() => void reload()}>
                  Загрузить актуальную версию
                </button>
              }
            >
              Событие или источник изменились. Проверьте актуальные данные перед повторным сохранением.
            </Banner>
          ) : null}
          {mutation.isError && !conflict ? <RequestError error={mutation.error} /> : null}
          <div className="event-actions">
            {initial.state !== 'cancelled' ? (
              <button className="btn on" type="submit">
                {initial.state === 'active' ? 'Сохранить изменения' : 'Сохранить черновик'}
              </button>
            ) : null}
            {features.events && initial.state !== 'active' ? (
              <button className="btn" type="button" onClick={() => save('active')}>
                Подтвердить публикацию
              </button>
            ) : null}
            {initial.state !== 'cancelled' ? (
              <button className="btn" type="button" onClick={() => setCancel(true)}>
                Отменить событие
              </button>
            ) : null}
          </div>
        </fieldset>
      </form>
      {cancel ? (
        <ConfirmModal
          icon="alert"
          title="Отменить событие?"
          body="Оно исчезнет из активного календаря. Опубликованная запись останется в истории до конца срока хранения."
          confirm="Отменить событие"
          cancel="Вернуться"
          onCancel={() => setCancel(false)}
          onConfirm={() => {
            setCancel(false);
            save('cancelled');
          }}
        />
      ) : null}
    </>
  );
}
