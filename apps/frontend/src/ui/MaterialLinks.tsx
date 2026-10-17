import type { MaterialLink } from '@studgroup/shared-types';
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

export function MaterialLinks({ items }: { items?: DisplayMaterial[] }) {
  if (items === undefined) return <p>Материалы пока не подключены.</p>;
  const valid = items.filter((item) => {
    try {
      const url = new URL(item.url);
      return url.protocol === 'https:' && !url.username && !url.password;
    } catch {
      return false;
    }
  });
  if (!valid.length) return <p>Материалы пока не добавлены.</p>;
  return (
    <ul className={styles.list}>
      {valid.map((item) => {
        const kind = item.kind ?? 'link';
        return (
          <li key={item.id}>
            <a
              className={styles.item}
              href={item.url}
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
