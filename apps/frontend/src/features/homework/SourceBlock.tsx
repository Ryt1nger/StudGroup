import type { SourceDetail } from '@studgroup/shared-types';
import { useId, useState } from 'react';
import clsx from 'clsx';
import { ru } from '../../i18n/ru';
import { formatShortDate, formatTime } from '../../lib/format';
import { isAllowedTelegramUrl } from '../../lib/telegramUrl';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Badge } from '../../ui/Badge';
import { ChevronDown, ExternalLink, TelegramMark } from '../../ui/icons';
import styles from './SourceBlock.module.css';

interface Props {
  sources: SourceDetail[];
  timeZone: string;
}

type SourceReason = keyof typeof ru.source.reason;

/**
 * Source action or the «Источник недоступен» explanation. Unavailable is a normal, calm state:
 * the card stays and safe metadata remains visible. Message excerpts are not rendered.
 */
function SourceRow({ source, timeZone }: { source: SourceDetail; timeZone: string }) {
  const { adapter } = useTelegram();
  const url = source.action.type === 'open_telegram_link' ? source.action.url : null;
  const canOpen = source.availability.state === 'available' && isAllowedTelegramUrl(url);
  const kindLabel = (ru.source.kind as Record<string, string | undefined>)[source.kind];
  const reasonKey = (source.availability.reason ?? 'unknown') as SourceReason;
  const reason = ru.source.reason[reasonKey] ?? ru.source.reason.unknown;

  return (
    <div className={styles.sourceItem}>
      {canOpen ? (
        <button type="button" className={styles.row} onClick={() => adapter?.openTelegramLink(url)}>
          <TelegramMark size={36} />
          <span className={styles.rowText}>
            <span className={styles.rowTitle}>{kindLabel ?? ru.source.openTelegram}</span>
            <span className={styles.rowHint}>{ru.source.openTelegram}</span>
          </span>
          <ExternalLink className={styles.external} size={20} />
        </button>
      ) : (
        <div className={styles.unavailable} role="note">
          <Badge tone="neutral">{ru.source.unavailable}</Badge>
          <p className={styles.reason}>{reason}</p>
          <dl className={styles.facts}>
            {kindLabel ? (
              <div>
                <dt className="sg-visually-hidden">{ru.detail.source}</dt>
                <dd>{kindLabel}</dd>
              </div>
            ) : null}
            {source.imported && source.kind !== 'imported_history' ? (
              <div>
                <dd>{ru.card.sourceImported}</dd>
              </div>
            ) : null}
          </dl>
        </div>
      )}
      {source.message_date ? (
        <p className={styles.date}>
          {ru.detail.received} {formatShortDate(source.message_date, timeZone)} ·{' '}
          {formatTime(source.message_date, timeZone)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The sources are secondary information: the section is collapsed by default and opens with the
 * arrow in its title. The state is local to the card page (not persisted); the content stays in
 * the DOM but hidden, so it is skipped by assistive technology until it is opened.
 */
export function SourceBlock({ sources, timeZone }: Props) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const title = sources.length > 1 ? ru.detail.sources : ru.detail.source;
  return (
    <section className={styles.block} aria-labelledby="source-title">
      <h2 id="source-title" className={styles.title}>
        <button
          type="button"
          className={styles.toggle}
          aria-expanded={open}
          aria-controls={listId}
          aria-label={`${title}: ${open ? ru.detail.sourcesHide : ru.detail.sourcesShow}`}
          onClick={() => setOpen((value) => !value)}
        >
          <span>{title}</span>
          <ChevronDown size={20} className={clsx(styles.chevron, open && styles.chevronOpen)} />
        </button>
      </h2>
      <div id={listId} className={styles.sourceList} hidden={!open}>
        {sources.map((source, index) => (
          <SourceRow
            key={`${source.kind}-${source.message_date ?? 'undated'}-${index}`}
            source={source}
            timeZone={timeZone}
          />
        ))}
      </div>
    </section>
  );
}
