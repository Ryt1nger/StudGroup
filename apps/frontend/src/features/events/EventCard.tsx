import type { EventPayload, PublishedEvent } from '@studgroup/shared-types';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { isApiRequestError } from '../../api/errors';
import { Float } from '../../shell/AppRoot';
import { ConfirmModal } from '../../ui/Blocks';
import { useFeedback } from '../../ui/Feedback';
import { useReviewEvent } from './api';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Ic } from '../../ui/Ic';
import { eventDate } from './calendar';

export function EventSource({ url }: { url?: string | null }) {
  const { adapter } = useTelegram();
  if (!url || !/^https:\/\/t\.me\/c\/\d+\/\d+$/.test(url)) return null;
  return (
    <a
      className="event-source"
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => {
        if (adapter) {
          event.preventDefault();
          adapter.openTelegramLink(url);
        }
      }}
    >
      <Ic name="tg" size={14} />
      Источник в Telegram
    </a>
  );
}

export function EventStatus({ item }: { item: { state: string; rescheduled?: boolean } }) {
  return (
    <span className={`chipx${item.state === 'cancelled' ? ' warn' : ''}`}>
      {item.state === 'cancelled'
        ? 'Отменено'
        : item.state === 'draft'
          ? 'Черновик'
          : item.rescheduled
            ? 'Перенесено'
            : 'Опубликовано'}
    </span>
  );
}

export function EventCard({
  item,
  timeZone,
  to,
}: {
  item: EventPayload & { id: string; state: string; rescheduled?: boolean; source_url?: string | null };
  timeZone: string;
  to?: string;
}) {
  return (
    <article className="block group-event-card">
      <Link className="group-event-main" to={to ?? `/events/${item.id}`}>
        <span className="group-event-label">
          <Ic name="cal" size={15} />
          Событие
        </span>
        <h3>{item.title}</h3>
        <p>
          <Ic name="clock" size={14} />
          {eventDate(item, timeZone)}
        </p>
        {item.place ? <p>{item.place}</p> : null}
        {item.description ? <p className="group-event-summary">{item.description}</p> : null}
        <EventStatus item={item} />
      </Link>
      <EventSource url={item.source_url} />
    </article>
  );
}

/** Permanent removal is distinct from cancelling an event in the public calendar. */
export function DeleteGroupEventButton({ item, backTo = '/events' }: { item: { id: string; revision: number; title: string }; backTo?: string }) {
  const [asking, setAsking] = useState(false);
  const navigate = useNavigate();
  const { toast } = useFeedback();
  const remove = useReviewEvent(item.id);
  return (
    <>
      <button type="button" className="icon-btn danger" aria-label="Удалить событие" disabled={remove.isPending} onClick={() => setAsking(true)}>
        <Ic name="trash" size={18} />
      </button>
      {asking ? (
        <Float>
          <ConfirmModal
            icon="trash"
            title="Удалить событие?"
            body="Событие исчезнет из календаря и истории у всех участников группы. Это действие нельзя отменить."
            confirm={remove.isPending ? 'Удаляем…' : 'Удалить'}
            cancel="Оставить"
            onCancel={() => setAsking(false)}
            onConfirm={() => {
              if (remove.isPending) return;
              remove.mutate(
                { expected_revision: item.revision, payload: { title: item.title }, state: 'deleted' },
                {
                  onSuccess: () => {
                    setAsking(false);
                    toast('Событие удалено');
                    void navigate(backTo, { replace: true });
                  },
                  onError: (error) => {
                    setAsking(false);
                    toast(isApiRequestError(error) && error.status === 409 ? 'Событие изменилось. Проверьте актуальную версию.' : 'Не удалось удалить событие');
                  },
                },
              );
            }}
          />
        </Float>
      ) : null}
    </>
  );
}

/** Calendar-only summary; full event information remains on the detail screen. */
export function ScheduleEventCards({ items, timeZone }: { items: readonly PublishedEvent[]; timeZone: string }) {
  return (
    <>
      {items.map((item) => (
        <Link key={item.id} className="schedule-event-card" to={`/events/${item.id}`}>
          <strong><Ic name="cal" size={15} />{item.title}</strong>
          <span>{eventDate(item, timeZone)}</span>
          {item.place ? <span>{item.place}</span> : null}
        </Link>
      ))}
    </>
  );
}

export function EventCards({ items, timeZone }: { items: readonly PublishedEvent[]; timeZone: string }) {
  return (
    <>
      {items.map((item) => (
        <EventCard key={item.id} item={item} timeZone={timeZone} />
      ))}
    </>
  );
}
