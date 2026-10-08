import { useState } from 'react';
import { useScheduleQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { formatDayKey, formatTime, weekRange } from '../../lib/format';
import { useServerNow } from '../../lib/hooks';
import { useSession, useSessionServerNow } from '../../session/SessionProvider';
import { useGroupTimeZone } from '../../session/useGroupTimeZone';
import { TopBar } from '../../shell/TopBar';
import { Banner } from '../../ui/Banner';
import { RequestError } from '../../ui/RequestError';
import { Skeleton } from '../../ui/Skeleton';
import { StateView } from '../../ui/StateView';
import { emptyIllustrations } from '../../ui/entityIcons';
import { ThemedImage } from '../../ui/ThemedImage';
import { Clock, Lock } from '../../ui/icons';
import { mapSchedule } from './mapSchedule';
import { scheduleAnchor } from './scheduleAnchor';
import { ScheduleScreenView } from './ScheduleScreenView';
import styles from './ScheduleScreenView.module.css';

function Loading() {
  return (
    <div role="status" aria-label={ru.states.loading} className={styles.loading}>
      <Skeleton height={18} width={160} />
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} height={84} radius={20} />
      ))}
    </div>
  );
}

/** Production `/schedule`: GET /v1/schedule for the current week in the group timezone. */
export function ScheduleScreen() {
  const timeZone = useGroupTimeZone();
  const { state } = useSession();
  const permissions = state.status === 'ready' ? state.session.permissions : [];
  const allowed = permissions.includes('schedule.read');
  // The window is fixed when the screen opens, from session.server_time (never the phone clock).
  const serverNow = useSessionServerNow();
  const [range] = useState(() => weekRange(serverNow() ?? Date.now(), timeZone));
  const query = useScheduleQuery(range.start, range.end, allowed);
  const data = query.data;
  const nowMs = useServerNow(data?.generated_at, query.dataUpdatedAt);

  if (!allowed) {
    return (
      <>
        <TopBar />
        <div className={styles.page}>
          <h1 className={styles.title}>{ru.nav.schedule}</h1>
          <StateView role="alert" icon={<Lock />} title={ru.schedule.forbiddenTitle} body={ru.schedule.forbiddenBody} />
        </div>
      </>
    );
  }

  const view = data ? mapSchedule(data, nowMs) : { days: [] };
  const rangeLabel = ru.schedule.weekRange(formatDayKey(range.start).split(', ')[1] ?? range.start, formatDayKey(range.end).split(', ')[1] ?? range.end);
  const withWeek = { ...view, weekLabel: view.weekLabel ? `${view.weekLabel} · ${rangeLabel}` : data ? rangeLabel : undefined };

  const clarify =
    data?.week_state === 'needs_clarification' ? (
      <Banner tone="warning" icon={<Clock />}>
        <strong>{ru.schedule.clarifyTitle}.</strong> {ru.schedule.clarifyBody}
      </Banner>
    ) : null;
  const stale = query.isError && data !== undefined;

  return (
    <ScheduleScreenView
      view={withWeek}
      initialAnchorId={data ? scheduleAnchor(data, nowMs) : undefined}
      notice={
        <>
          {clarify}
          {stale ? <Banner tone="warning" icon={<Clock />}>{ru.freshness.stale(formatTime(data.generated_at, data.group_timezone))}</Banner> : null}
        </>
      }
    >
      {query.isPending ? <Loading /> : null}
      {query.isError && !data ? <RequestError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {data && data.lessons.length === 0 ? (
        <StateView illustration={<ThemedImage asset={emptyIllustrations.noLessons} />} title={ru.schedule.emptyTitle} body={ru.schedule.emptyBody} />
      ) : null}
    </ScheduleScreenView>
  );
}
