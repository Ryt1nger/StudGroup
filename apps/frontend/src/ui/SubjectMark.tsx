import { subjectGlyph } from '../lib/subjectColor';

/** One stroke icon per field of study. The path data is static; it never carries user text. */
const ICONS = {
  math: '<path d="M18 5H7l6 7-6 7h11"/>',
  physics: '<circle cx="12" cy="12" r="1.6"/><ellipse cx="12" cy="12" rx="10" ry="4"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(60 12 12)"/><ellipse cx="12" cy="12" rx="10" ry="4" transform="rotate(120 12 12)"/>',
  chemistry: '<path d="M9 3h6M10 3v6l-5.5 9.5A2 2 0 0 0 6.2 21h11.6a2 2 0 0 0 1.7-2.5L14 9V3M7.5 15h9"/>',
  biology: '<path d="M5 19c0-8 5-13 14-14 0 9-5 14-13 14zM5 19c3-4 6-7 9-9"/>',
  history: '<path d="M3 9l9-5 9 5M5 9v9M9.5 9v9M14.5 9v9M19 9v9M3 21h18"/>',
  geography: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
  literature: '<path d="M12 6.6C10.2 5.2 7.8 4.7 4.5 4.8v12.4c3.3-.1 5.7.4 7.5 1.8 1.8-1.4 4.2-1.9 7.5-1.8V4.8c-3.3-.1-5.7.4-7.5 1.8zM12 6.6V19"/>',
  language: '<path d="M4 6h9M8.5 4v2M6 6c0 4 3 7 6 8M12 6c-1 4-4 7-8 9M14 20l4-10 4 10M15.5 17h5"/>',
  computing: '<path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
  economics: '<path d="M4 20V4M4 20h16M8 15l4-4 3 3 5-6"/>',
  finance: '<circle cx="12" cy="12" r="9"/><path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5-1.5 0-2.5.8-2.5 2s1 1.6 2.5 2 2.5.8 2.5 2-1 2-2.5 2c-1 0-2-.5-2.5-1.5M12 6v2M12 16v2"/>',
  law: '<path d="M12 4v16M7 20h10M5 8h14M5 8l-3 7a3 3 0 0 0 6 0zM19 8l-3 7a3 3 0 0 0 6 0z"/>',
  philosophy: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z"/>',
  psychology: '<path d="M9 5a3 3 0 0 0-3 3 3 3 0 0 0-1 5 3 3 0 0 0 2 5 3 3 0 0 0 5 1V5a3 3 0 0 0-3 0zM15 5a3 3 0 0 1 3 3 3 3 0 0 1 1 5 3 3 0 0 1-2 5 3 3 0 0 1-5 1"/>',
  society: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6 6 0 0 1 3.5 5.5"/>',
  sport: '<path d="M6 8v8M3 10v4M18 8v8M21 10v4M6 12h12"/>',
  art: '<path d="M12 3a9 9 0 1 0 0 18c1.4 0 2-1 1.5-2-.6-1.2.2-2.5 1.6-2.5H17a4 4 0 0 0 4-4c0-5-4-9.5-9-9.5z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7.5" r="1"/>',
  music: '<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  medicine: '<path d="M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z"/>',
  engineering: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
  marketing: '<path d="M3 11v2a1 1 0 0 0 1 1h3l6 4V6L7 10H4a1 1 0 0 0-1 1zM16 9a4 4 0 0 1 0 6"/>',
  management: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3 13h18"/>',
  statistics: '<path d="M5 20V10M12 20V4M19 20v-7"/>',
  politics: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  culture: '<path d="M12 3l2.5 5.5L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.5-.5z"/>',
  safety: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4"/>',
  architecture: '<path d="M4 21V8l8-5 8 5v13M9 21v-6h6v6M4 21h16"/>',
  design: '<path d="M4 20l1-4L16 5l3 3L8 19zM14 7l3 3"/>',
  digital: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
  ecology: '<path d="M12 21v-6M6 15h12l-3.5-5H16l-4-7-4 7h1.5z"/>',
  communication: '<path d="M4 5h16v11H9l-5 4z"/>',
  transport: '<rect x="4" y="5" width="16" height="12" rx="3"/><path d="M4 12h16M8 20l1-3M16 20l-1-3M8 14.5h.01M16 14.5h.01"/>',
  agro: '<path d="M12 21V9M12 13c-3 0-5-2-5-5 3 0 5 2 5 5zM12 11c0-3 2-5 5-5 0 3-2 5-5 5z"/>',
  astronomy: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/>',
} as const;

type Mark = keyof typeof ICONS;

/** Keyword stems (lower case, Russian and English) → the icon of that field. First match wins. */
const RULES: ReadonlyArray<readonly [RegExp, Mark]> = [
  [/(цифров|информац|digital|it-|ит\b)/, 'digital'],
  [/(програм|информатик|алгоритм|баз[аы] данн|компьютер|вычисл|кибер|нейрон|искусственн|machine|coding|computer|software|data)/, 'computing'],
  [/(матем|алгебр|геометр|анализ|дискрет|вероятн|логик|calculus|algebra|math)/, 'math'],
  [/(статист|эконометр|statistic)/, 'statistics'],
  [/(физик|механик|оптик|термодин|электро(?!нн)|квант|physics)/, 'physics'],
  [/(астроном|космос|astronom)/, 'astronomy'],
  [/(хими|химич|chemistry)/, 'chemistry'],
  [/(биолог|генет|анатом|зоолог|ботан|микробиол|biolog)/, 'biology'],
  [/(экологи|природопольз|ландшафт|ecolog)/, 'ecology'],
  [/(агро|сельск|почвов|растениевод)/, 'agro'],
  [/(медиц|клиник|фармак|хирург|лечебн|стоматолог|здоровь|medic)/, 'medicine'],
  [/(психолог|педагог|когнитив|psycholog)/, 'psychology'],
  [/(философ|этик|логика мышл|культурологи|религ|philosoph)/, 'philosophy'],
  [/(социолог|обществ|социал|sociolog)/, 'society'],
  [/(истори|археолог|краеведен|history)/, 'history'],
  [/(географ|геолог|картограф|geograph)/, 'geography'],
  [/(государствен|политолог|политик|международн|дипломат|politic)/, 'politics'],
  [/(право|правов|юрид|закон|криминал|судопроиз|конституц|law\b)/, 'law'],
  [/(финанс|бухгалт|учёт|учет|аудит|налог|банк|кредит|инвестиц|страхов|finance)/, 'finance'],
  [/(эконом|рынк|микроэкон|макроэкон|торгов|economic)/, 'economics'],
  [/(маркетинг|реклам|бренд|pr\b|продвижен|продаж|marketing)/, 'marketing'],
  [/(менеджм|управлен|логистик|предпринимат|бизнес|проектн|management)/, 'management'],
  [/(литератур|русский язык|культура речи|риторик|стилист|филолог|журналист|literature)/, 'literature'],
  [/(иностран|англ|немец|француз|испанск|китайск|японск|итальянск|латин|язык|лингвист|перевод|language|english)/, 'language'],
  [/(коммуник|переговор|делов|общени|речь|communicat)/, 'communication'],
  [/(физкульт|физическ|спорт|физ-ра|физра|здоровый образ|фитнес|sport)/, 'sport'],
  [/(музык|вокал|хор|сольфедж|music)/, 'music'],
  [/(искусств|рисун|живопис|композиц|художеств|кино|театр|art\b)/, 'art'],
  [/(дизайн|график|типограф|design)/, 'design'],
  [/(архитект|строител|градостро|architect)/, 'architecture'],
  [/(безопасн|жизнедеят|бжд|охран|защита информ|cyber|safety)/, 'safety'],
  [/(транспорт|автомоб|авиа|судов|железнодорож|transport)/, 'transport'],
  [/(инженер|техническ|механизм|сопротивление|материал|электротех|робот|автоматиз|engineering)/, 'engineering'],
  [/(культур|мировая художественная|искусствоведен)/, 'culture'],
];

export function subjectMarkName(name: string): Mark | null {
  const text = name.toLowerCase();
  for (const [rule, mark] of RULES) if (rule.test(text)) return mark;
  return null;
}

/** The icon of a subject, or its first letter when no field of study matches. */
export function SubjectMark({ name, size }: { name: string; size?: number }) {
  const mark = subjectMarkName(name);
  if (!mark) return <>{subjectGlyph(name)}</>;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      dangerouslySetInnerHTML={{ __html: ICONS[mark] }}
    />
  );
}
