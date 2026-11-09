import type { Accent } from '../telegram/TelegramProvider';

export const ACCENT_INFO: Record<Accent, { name: string; h: number; s: string; l: string; on?: string }> = {
  green: { name: 'Зелёный', h: 150, s: '60%', l: '42%' },
  red: { name: 'Красный', h: 355, s: '86%', l: '60%' },
  violet: { name: 'Фиолетовый', h: 246, s: '100%', l: '68%' },
  blue: { name: 'Голубой', h: 212, s: '100%', l: '58%' },
  gray: { name: 'Серый', h: 230, s: '8%', l: '56%' },
  yellow: { name: 'Жёлтый', h: 44, s: '100%', l: '52%', on: '#1c1500' },
  pink: { name: 'Нежно-розовый', h: 335, s: '82%', l: '78%', on: '#24101b' },
  teal: { name: 'Бирюзовый', h: 177, s: '64%', l: '55%', on: '#062321' },
};

export const THEMES = [
  ['light', 'Светлая'],
  ['dark', 'Тёмная'],
  [null, 'Системная'],
] as const;

export const TEXT_SIZES = [['s', 'Мелкий'], ['m', 'Обычный'], ['l', 'Крупный']] as const;
export const DENSITIES = [['normal', 'Обычная'], ['compact', 'Компактная']] as const;
