import clsx from 'clsx';
import { useMutation } from '@tanstack/react-query';
import { useId, useState, type ReactNode } from 'react';
import type { SourceDetail } from '@studgroup/shared-types';
import { ru } from '../../i18n/ru';
import { formatShortDate, formatTime } from '../../lib/format';
import { isAllowedTelegramUrl } from '../../lib/telegramUrl';
import { useTelegram } from '../../telegram/TelegramProvider';
import { Ic } from '../../ui/Ic';
import type { LearningCardUpdateView } from '../homework/cardViewModel';

/** A collapsible block: the title is a button with an arrow; the content stays in the DOM but hidden. */
export function Collapsible({ title, label, children }: { title: string; label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="block">
      <h3 className="srch">
        <button type="button" className="srctog" aria-expanded={open} aria-controls={id} aria-label={`${label}: ${open ? ru.detail.sourcesHide : ru.detail.sourcesShow}`} onClick={() => setOpen((v) => !v)}>
          <span>{title}</span>
          <span className={clsx('cv', open && 'open')}><Ic name="chevd" size={20} /></span>
        </button>
      </h3>
      <div id={id} className="srclist" hidden={!open}>{children}</div>
    </div>
  );
}

export function SourceRow({ source, timeZone }: { source: SourceDetail; timeZone: string }) {
  const { adapter } = useTelegram();
  const url = source.action.type === 'open_telegram_link' ? source.action.url : null;
  const canOpen = source.availability.state === 'available' && isAllowedTelegramUrl(url);
  const kindLabel = (ru.source.kind as Record<string, string | undefined>)[source.kind];
  const reasonKey = (source.availability.reason ?? 'unknown') as keyof typeof ru.source.reason;
  const reason = ru.source.reason[reasonKey] ?? ru.source.reason.unknown;
  const date = source.message_date
    ? `${ru.detail.received} ${formatShortDate(source.message_date, timeZone)} · ${formatTime(source.message_date, timeZone)}`
    : null;
  if (!canOpen) {
    return (
      <div className="src na" role="note">
        <span className="chipx mute">{ru.source.unavailable}</span>
        <p>{reason}</p>
        {kindLabel || date ? <p className="sd">{[kindLabel, date].filter(Boolean).join(' · ')}</p> : null}
      </div>
    );
  }
  return (
    <div className="src">
      <button type="button" className="srcbtn" onClick={() => adapter?.openTelegramLink(url)}>
        <span className="tgm"><Ic name="tg" size={24} /></span>
        <span className="srct"><b>{kindLabel ?? ru.source.openTelegram}</b><i>{ru.source.openTelegram}</i></span>
        <Ic name="ext" size={18} />
      </button>
      {date ? <p className="sd">{date}</p> : null}
    </div>
  );
}

export function UpdatesBlock({ items, timeZone }: { items: LearningCardUpdateView[]; timeZone: string }) {
  if (!items.length) return null;
  return (
    <Collapsible title={ru.detail.updates} label={ru.detail.updates}>
      <ul className="upd">
        {items.map((item) => (
          <li key={item.id}>
            <span className={clsx('uk', item.kind)}>{ru.detail.updateKind[item.kind]}</span>
            <span className="ut2">{item.text}</span>
            {item.createdAt ? <time dateTime={item.createdAt}>{formatShortDate(item.createdAt, timeZone)} · {formatTime(item.createdAt, timeZone)}</time> : null}
          </li>
        ))}
      </ul>
    </Collapsible>
  );
}

/** «Добавить файл»: the file stays in Telegram, so this opens the bot, which waits for it. */
export function AddFile({ fetchLink }: { fetchLink: () => Promise<string> }) {
  const { adapter } = useTelegram();
  const open = useMutation({
    mutationFn: fetchLink,
    onSuccess: (url) => {
      if (isAllowedTelegramUrl(url)) adapter?.openTelegramLink(url);
    },
  });
  return (
    <div className="erow">
      <button type="button" className={clsx('btn on sbtn2', open.isPending && 'busy')} disabled={open.isPending} onClick={() => open.mutate()}>
        <Ic name="clip" size={18} />
        {ru.edit.addFile}
      </button>
      {open.isError ? <p className="ferr" role="alert">{ru.edit.errors.fileLink}</p> : null}
    </div>
  );
}

