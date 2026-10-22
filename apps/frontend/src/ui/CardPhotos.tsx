import type { MaterialLink } from '@studgroup/shared-types';
import { useEffect, useState } from 'react';
import { useAttachmentImageQuery } from '../api/queries';
import { ru } from '../i18n/ru';
import { Button } from './Button';
import { Skeleton } from './Skeleton';
import styles from './CardPhotos.module.css';

function CardPhoto({ item, alt }: { item: MaterialLink; alt: string }) {
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

  if (query.isError) {
    return (
      <div className={styles.failed} role="alert">
        <p>{ru.card.photoUnavailable}</p>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          {ru.card.photoRetry}
        </Button>
      </div>
    );
  }
  if (!url) return <Skeleton height={240} radius={16} />;
  return (
    <>
      <button
        type="button"
        className={styles.thumb}
        onClick={() => setOpen(true)}
        aria-label={ru.card.photoOpen}
      >
        <img src={url} alt={alt} className={styles.image} />
      </button>
      {open ? (
        <div className={styles.overlay} role="dialog" aria-modal="true" aria-label={alt}>
          <button
            type="button"
            className={styles.close}
            onClick={() => setOpen(false)}
            aria-label={ru.card.photoClose}
          >
            ×
          </button>
          <img src={url} alt={alt} className={styles.full} />
        </div>
      ) : null}
    </>
  );
}

export function CardPhotos({ items, alt }: { items: MaterialLink[]; alt: string }) {
  return (
    <div className={styles.list}>
      {items.map((item) => (
        <CardPhoto key={item.id} item={item} alt={alt} />
      ))}
    </div>
  );
}
