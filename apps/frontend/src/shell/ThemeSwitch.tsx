import type { ColorScheme } from '../telegram/adapter';
import { Moon, Sun } from '../ui/icons';
import styles from './ThemeSwitch.module.css';

export function ThemeSwitch({ scheme, onChange }: { scheme: ColorScheme; onChange: (scheme: ColorScheme) => void }) {
  const dark = scheme === 'dark';
  return <div className={styles.row}>
    <span>Тема</span>
    <button type="button" className={styles.switch} role="switch" aria-label="Тёмная тема"
      aria-checked={dark} onClick={() => onChange(dark ? 'light' : 'dark')}>
      <span className={styles.thumb} />
      <span className={styles.sun}><Sun size={22} /></span>
      <span className={styles.moon}><Moon size={22} /></span>
    </button>
  </div>;
}
