import { ru } from '../i18n/ru';
import { StateView } from '../ui/StateView';
import { emptyIllustrations } from '../ui/entityIcons';
import { ThemedImage } from '../ui/ThemedImage';
import { TopBar } from './TopBar';
import styles from './PreparedSection.module.css';

/** «Раздел готовится»: a deliberate placeholder, never populated with fake academic data. */
export function PreparedSection({ title }: { title: string }) {
  return (
    <>
      <TopBar />
      <div className={styles.page}>
        <h1 className={styles.title}>{title}</h1>
        <StateView
          illustration={<ThemedImage asset={emptyIllustrations.preparing} />}
          title={ru.prepared.title}
          body={ru.prepared.body}
        />
      </div>
    </>
  );
}
