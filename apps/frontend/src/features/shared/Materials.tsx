import { client, type MaterialLink } from '@studgroup/shared-types';
import { useEffect, useState } from 'react';
import { useAttachmentImageQuery } from '../../api/queries';
import { ru } from '../../i18n/ru';
import { Ic } from '../../ui/Ic';
import { Skeleton } from '../../ui/Blocks';

type MaterialKind = NonNullable<MaterialLink['kind']>;

const LABEL: Record<MaterialKind, string> = {
  photo: 'Фотография',
  document: 'Документ',
  link: 'Ссылка',
  video: 'Видео',
  audio: 'Аудиозапись',
  voice: 'Голосовое сообщение',
  animation: 'Анимация',
};

/**
 * A file kept in Telegram arrives as an API-relative signed path (`/v1/files/...`); it is opened on the
 * API origin. Anything else must be an absolute https link without credentials.
 */
function materialHref(raw: string): string | null {
  try {
    if (raw.startsWith('/v1/files/')) {
      const base = client.getConfig().baseUrl;
      if (!base) return null;
      const origin = new URL(base);
      return origin.protocol === 'https:' ? new URL(raw, origin.origin).href : null;
    }
    const url = new URL(raw);
    return url.protocol === 'https:' && !url.username && !url.password ? raw : null;
  } catch {
    return null;
  }
}

/** The «Материалы» list: files and links as buttons-looking anchors. */
export function MaterialList({ items }: { items?: MaterialLink[] }) {
  if (items === undefined) return <p className="muted" style={{ margin: 0 }}>Материалы пока не подключены.</p>;
  const valid = items.flatMap((item) => {
    const href = materialHref(item.url);
    return href ? [{ item, href }] : [];
  });
  if (!valid.length) return <p className="muted" style={{ margin: 0 }}>Материалы пока не добавлены.</p>;
  return (
    <ul className="mats">
      {valid.map(({ item, href }) => {
        const kind = item.kind ?? 'link';
        return (
          <li key={item.id}>
            <a className="mat" href={href} target="_blank" rel="noopener noreferrer" aria-label={item.title}>
              <span className="mi"><Ic name={kind === 'link' ? 'ext' : 'dl'} size={20} /></span>
              <span className="mt">
                <b>{item.title}</b>
                <i>{LABEL[kind]}</i>
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function Photo({ item, alt }: { item: MaterialLink; alt: string }) {
  const query = useAttachmentImageQuery(item.id);
  const [url, setUrl] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const blob = query.data;
    if (!blob) return undefined;
    let cancelled = false;
    const objectUrl = URL.createObjectURL(blob);
    // Deferred so the effect body never sets state synchronously.
    void Promise.resolve().then(() => {
      if (!cancelled) setUrl(objectUrl);
    });
    return () => {
      cancelled = true;
      URL.revokeObjectURL(objectUrl);
    };
  }, [query.data]);

  useEffect(() => {
    if (!open) return;
    const key = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, [open]);

  if (query.isError) {
    return (
      <div className="pfail" role="alert">
        <p>{ru.card.photoUnavailable}</p>
        <button type="button" className="btn on sbtn" onClick={() => void query.refetch()}>{ru.card.photoRetry}</button>
      </div>
    );
  }
  if (!url) return <Skeleton count={1} label={ru.states.loading} />;
  return (
    <>
      <button type="button" className="thumb" onClick={() => setOpen(true)} aria-label={ru.card.photoOpen}>
        <img src={url} alt={alt} />
      </button>
      {open ? (
        <div className="photo-view" role="dialog" aria-modal="true" aria-label={alt} onClick={(event) => event.target === event.currentTarget && setOpen(false)}>
          <button type="button" className="pclose" onClick={() => setOpen(false)} aria-label={ru.card.photoClose}>×</button>
          <img src={url} alt={alt} />
        </div>
      ) : null}
    </>
  );
}

/** Photos that are the task itself: shown instead of a text description. */
export function Photos({ items, alt }: { items: MaterialLink[]; alt: string }) {
  return (
    <div className="photos">
      {items.map((item) => (
        <Photo key={item.id} item={item} alt={alt} />
      ))}
    </div>
  );
}
