import { client, type MaterialLink } from '@studgroup/shared-types';
import { createPortal } from 'react-dom';
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

/** Only links that the user can actually open count as material content. */
export function hasVisibleMaterials(items?: MaterialLink[]): boolean {
  return items?.some((item) => materialHref(item.url) !== null) ?? false;
}

/** The «Материалы» list: files and links as buttons-looking anchors. */
export function MaterialList({ items }: { items?: MaterialLink[] }) {
  if (items === undefined)
    return (
      <p className="muted" style={{ margin: 0 }}>
        Материалы пока не подключены.
      </p>
    );
  const valid = items.flatMap((item) => {
    const href = materialHref(item.url);
    return href ? [{ item, href }] : [];
  });
  if (!valid.length)
    return (
      <p className="muted" style={{ margin: 0 }}>
        Материалы пока не добавлены.
      </p>
    );
  return (
    <ul className="mats">
      {valid.map(({ item, href }) => {
        const kind = item.kind ?? 'link';
        return (
          <li key={item.id}>
            <a className="mat" href={href} target="_blank" rel="noopener noreferrer" aria-label={item.title}>
              <span className="mi">
                <Ic name={kind === 'link' ? 'ext' : 'dl'} size={20} />
              </span>
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

function Photo({ item, alt, onOpen }: { item: MaterialLink; alt: string; onOpen: (url: string) => void }) {
  const query = useAttachmentImageQuery(item.id);
  const [url, setUrl] = useState<string | null>(null);

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

  if (query.isError) {
    return (
      <div className="pfail" role="alert">
        <p>{ru.card.photoUnavailable}</p>
        <button type="button" className="btn on sbtn" onClick={() => void query.refetch()}>
          {ru.card.photoRetry}
        </button>
      </div>
    );
  }
  if (!url) return <Skeleton count={1} label={ru.states.loading} />;
  return (
    <>
      <button type="button" className="thumb" onClick={() => onOpen(url)} aria-label={ru.card.photoOpen}>
        <img src={url} alt={alt} />
      </button>
    </>
  );
}

/** Photos that are the task itself: shown instead of a text description. */
export function Photos({ items, alt }: { items: MaterialLink[]; alt: string }) {
  const [active, setActive] = useState<number | null>(null);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [zoom, setZoom] = useState(1);
  const [startX, setStartX] = useState<number | null>(null);
  useEffect(() => {
    if (active === null) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActive(null);
      if (event.key === 'ArrowLeft') setActive((value) => (value === null ? value : Math.max(0, value - 1)));
      if (event.key === 'ArrowRight')
        setActive((value) => (value === null ? value : Math.min(items.length - 1, value + 1)));
    };
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
      document.body.style.overflow = previous;
    };
  }, [active, items.length]);
  const show = (index: number, url: string) => {
    setUrls((value) => ({ ...value, [items[index]!.id]: url }));
    setZoom(1);
    setActive(index);
  };
  const move = (direction: -1 | 1) =>
    setActive((value) =>
      value === null ? value : Math.max(0, Math.min(items.length - 1, value + direction)),
    );
  return (
    <div className="photos">
      {items.map((item, index) => (
        <Photo key={item.id} item={item} alt={alt} onOpen={(url) => show(index, url)} />
      ))}
      {active !== null && urls[items[active]!.id]
        ? createPortal(
            <div
              className="photo-view"
              role="dialog"
              aria-modal="true"
              aria-label={alt}
              onClick={(event) => event.target === event.currentTarget && setActive(null)}
              onTouchStart={(event) => setStartX(event.touches[0]?.clientX ?? null)}
              onTouchEnd={(event) => {
                if (
                  startX !== null &&
                  event.changedTouches[0] &&
                  Math.abs(event.changedTouches[0].clientX - startX) > 48
                )
                  move(event.changedTouches[0].clientX > startX ? -1 : 1);
                setStartX(null);
              }}
              onWheel={(event) => {
                event.preventDefault();
                setZoom((value) => Math.min(4, Math.max(1, value + (event.deltaY < 0 ? 0.2 : -0.2))));
              }}
            >
              <button
                type="button"
                className="pclose"
                onClick={() => setActive(null)}
                aria-label={ru.card.photoClose}
              >
                ×
              </button>
              {items.length > 1 ? (
                <>
                  <button
                    type="button"
                    className="photo-arrow photo-arrow-left"
                    onClick={() => move(-1)}
                    disabled={active === 0}
                    aria-label="Предыдущее фото"
                  >
                    ‹
                  </button>
                  <button
                    type="button"
                    className="photo-arrow photo-arrow-right"
                    onClick={() => move(1)}
                    disabled={active === items.length - 1}
                    aria-label="Следующее фото"
                  >
                    ›
                  </button>
                  <div className="photo-dots" aria-label="Фото в карточке">
                    {items.map((item, index) => (
                      <button
                        key={item.id}
                        type="button"
                        className={index === active ? 'on' : ''}
                        onClick={() => setActive(index)}
                        aria-label={`Фото ${index + 1}`}
                      />
                    ))}
                  </div>
                </>
              ) : null}
              <img
                className="photo-zoom"
                src={urls[items[active]!.id]}
                alt={alt}
                style={{ transform: `scale(${zoom})` }}
              />
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
