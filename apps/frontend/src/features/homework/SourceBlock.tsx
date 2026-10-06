import type { HomeworkDetail } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { isAllowedTelegramUrl } from '../../lib/telegramUrl';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Badge } from '../../ui/Badge';
import { ExternalLink, TelegramMark } from '../../ui/icons';
import styles from './SourceBlock.module.css';

interface Props {
  detail: HomeworkDetail;
}

type SourceReason = keyof typeof ru.source.reason;

/**
 * Source action or the «Источник недоступен» explanation. Unavailable is a normal, calm state:
 * the card stays and safe metadata remains visible. Message excerpts are not rendered.
 */
export function SourceBlock({ detail }: Props) {
  const { adapter } = useTelegram();
  const source = detail.source_detail;
  const url = source.action.type === 'open_telegram_link' ? source.action.url : null;
  const canOpen = source.availability.state === 'available' && isAllowedTelegramUrl(url);
  const kindLabel = (ru.source.kind as Record<string, string | undefined>)[source.kind];
  const reasonKey = (source.availability.reason ?? 'unknown') as SourceReason;
  const reason = ru.source.reason[reasonKey] ?? ru.source.reason.unknown;

  return (
    <section className={styles.block} aria-labelledby="source-title">
      <h2 id="source-title" className={styles.title}>
        {ru.detail.source}
      </h2>

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

    </section>
  );
}
