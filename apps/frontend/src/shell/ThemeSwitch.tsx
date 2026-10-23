import type { ColorScheme } from '../telegram/adapter';
import { Moon, Sun } from '../ui/icons';
import { ACCENTS, useTelegram, type Accent } from '../telegram/TelegramProvider';
import styles from './ThemeSwitch.module.css';

const ACCENT_LABELS: Record<Accent, string> = {
  violet: 'Фиолетовый',
  blue: 'Голубой',
  green: 'Зелёный',
  red: 'Красный',
  yellow: 'Жёлтый',
  gray: 'Серый',
};

export function AccentPicker() {
  const { accent, setAccent } = useTelegram();
  return (
    <div className={styles.accentBlock}>
      <span>Цветовой акцент</span>
      <div className={styles.swatches} role="radiogroup" aria-label="Цветовой акцент">
        {ACCENTS.map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={accent === value}
            aria-label={ACCENT_LABELS[value]}
            data-accent={value}
            className={styles.swatch}
            onClick={() => setAccent(value)}
          />
        ))}
      </div>
    </div>
  );
}

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
