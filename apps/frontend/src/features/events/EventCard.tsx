import type { EventPayload, PublishedEvent } from '@studgroup/shared-types';
import { Link } from 'react-router';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Ic } from '../../ui/Ic';
import { eventDate } from './calendar';
import { EventCalendarAction } from './EventCalendarAction';

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
  calendarItem,
}: {
  item: EventPayload & { id: string; state: string; rescheduled?: boolean; source_url?: string | null };
  timeZone: string;
  to?: string;
  calendarItem?: PublishedEvent;
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
      {calendarItem ? <EventCalendarAction item={calendarItem} /> : null}
    </article>
  );
}

export function EventCards({ items, timeZone, calendarAction = false }: { items: readonly PublishedEvent[]; timeZone: string; calendarAction?: boolean }) {
  return (
    <>
      {items.map((item) => (
        <EventCard key={item.id} item={item} timeZone={timeZone} calendarItem={calendarAction ? item : undefined} />
      ))}
    </>
  );
}
