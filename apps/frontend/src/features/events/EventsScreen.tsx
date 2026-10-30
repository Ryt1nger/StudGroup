import { Link, useParams, useSearchParams } from 'react-router';
import { useServerNow } from '../../lib/hooks';
import { useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { BackTop, SectionTop } from '../../shell/TopBar';
import { Banner, Empty, Skeleton, StateView } from '../../ui/Blocks';
import { RequestError } from '../../ui/RequestError';
import { EventCard, EventCards, EventSource, EventStatus } from './EventCard';
import { useEventCapabilities, useEventReview, useGroupEvent, useGroupEvents } from './api';
import { eventDate, eventInHistory } from './calendar';

const unavailable = (
  <StateView icon="lock" title="События недоступны" body="Раздел пока не включён для вашей группы." />
);

export function EventsScreen() {
  const features = useEventCapabilities();
  const [params, setParams] = useSearchParams();
  const history = params.get('history') === '1';
  const query = useGroupEvents({ history });
  const timeZone = useGroupTimeZone();
  const deviceTick = useServerNow(undefined, 0);
  const serverNow = useSessionServerNow();
  const nowMs = serverNow() ?? deviceTick;
  const items = (query.data?.events ?? []).filter(
    (item) => !history || eventInHistory(item, nowMs, timeZone),
  );
  return (
    <>
      <SectionTop title="События" />
      {features.event_review ? (
        <Link className="pillb" to="/events/review">
          Проверить черновики и изменения
        </Link>
      ) : null}
      {!features.events ? (
        unavailable
      ) : (
        <>
          <div className="seg" role="group" aria-label="Период событий">
            <button
              type="button"
              className={!history ? 'on' : ''}
              aria-pressed={!history}
              onClick={() => setParams({})}
            >
              Предстоящие
            </button>
            <button
              type="button"
              className={history ? 'on' : ''}
              aria-pressed={history}
              onClick={() => setParams({ history: '1' })}
            >
              История
            </button>
          </div>
          {query.isPending ? <Skeleton label="Загрузка событий" /> : null}
          {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
          {query.data && !items.length ? (
            <Empty
              title={history ? 'История пуста' : 'Пока нет событий'}
              body={
                history
                  ? 'Здесь появятся завершённые и отменённые события в пределах срока хранения.'
                  : 'Подтверждённые мероприятия появятся здесь и в календаре.'
              }
            />
          ) : null}
          <EventCards items={items} timeZone={timeZone} />
        </>
      )}
    </>
  );
}

export function GroupEventScreen() {
  const { eventId = '' } = useParams();
  const features = useEventCapabilities();
  const query = useGroupEvent(eventId);
  const item = query.data;
  const timeZone = useGroupTimeZone();
  return (
    <>
      <BackTop
        fallback="/events"
        right={
          features.event_review ? (
            <Link className="pillb" to={`/events/review/${eventId}`}>
              Редактировать
            </Link>
          ) : undefined
        }
      />
      {!features.events ? (
        unavailable
      ) : (
        <>
          {query.isPending ? <Skeleton label="Загрузка события" /> : null}
          {query.isError ? (
            <RequestError error={query.error} onRetry={() => void query.refetch()} homeLink />
          ) : null}
          {item ? (
            <>
              <div className="d-head">
                <h1>{item.title}</h1>
                <EventStatus item={item} />
                <p>{eventDate(item, timeZone)}</p>
              </div>
              {item.state === 'cancelled' ? (
                <Banner tone="warning" icon="alert">
                  Мероприятие отменено
                </Banner>
              ) : null}
              <div className="block">
                <h3>О событии</h3>
                <p className="event-description">{item.description || 'Описание не указано'}</p>
              </div>
              <div className="block">
                <h3>Место</h3>
                <p>{item.place || 'Место уточняется'}</p>
              </div>
              {item.conditions ? (
                <div className="block">
                  <h3>Условия участия</h3>
                  <p className="event-description">{item.conditions}</p>
                </div>
              ) : null}
              {item.audience_label ? (
                <div className="block">
                  <h3>Аудитория</h3>
                  <p>{item.audience_label}</p>
                </div>
              ) : null}
              <div className="block">
                <EventSource url={item.source_url} />
                {!item.source_url ? (
                  <p>Источник больше недоступен. Подтверждённые сведения сохранены.</p>
                ) : null}
              </div>
            </>
          ) : null}
        </>
      )}
    </>
  );
}

export function EventReviewScreen() {
  const query = useEventReview();
  const features = useEventCapabilities();
  const timeZone = useGroupTimeZone();
  return (
    <>
      <BackTop fallback="/events" />
      <h1 className="ptitle">Проверка событий</h1>
      {!features.event_review ? (
        unavailable
      ) : (
        <>
          {query.isPending ? <Skeleton label="Загрузка черновиков" /> : null}
          {query.isError ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
          {query.data?.events.length === 0 ? (
            <Empty title="Нет черновиков" body="Распознанные афиши появятся здесь для проверки." />
          ) : null}
          {query.data?.events.map((item) => (
            <EventCard key={item.id} item={item} timeZone={timeZone} to={`/events/review/${item.id}`} />
          ))}
        </>
      )}
    </>
  );
}
