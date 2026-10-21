import { client, type MaterialLink } from '@studgroup/shared-types';
import { Download, ExternalLink } from './icons';
import styles from './MaterialLinks.module.css';

type MaterialKind = NonNullable<MaterialLink['kind']>;
type DisplayMaterial = MaterialLink;

const labels: Record<MaterialKind, string> = {
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

export function MaterialLinks({ items }: { items?: DisplayMaterial[] }) {
  if (items === undefined) return <p>Материалы пока не подключены.</p>;
  const valid = items.flatMap((item) => {
    const href = materialHref(item.url);
    return href ? [{ item, href }] : [];
  });
  if (!valid.length) return <p>Материалы пока не добавлены.</p>;
  return (
    <ul className={styles.list}>
      {valid.map(({ item, href }) => {
        const kind = item.kind ?? 'link';
        return (
          <li key={item.id}>
            <a
              className={styles.item}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={item.title}
            >
              <span className={styles.icon}>
                {kind === 'link' ? <ExternalLink size={20} /> : <Download size={20} />}
              </span>
              <span className={styles.text}>
                <span className={styles.title}>{item.title}</span>
                <span className={styles.kind}>{labels[kind]}</span>
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}
