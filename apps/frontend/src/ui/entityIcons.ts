import { assets, type ThemedAsset } from '../assets';

/**
 * Single source of truth: entity / action / state → designer asset (both themes come from the ThemedAsset).
 * Identical entities use identical assets on every screen. Urgency is decided ONLY by the contract's `urgency` field.
 * The full table is mirrored in DESIGN_HANDOFF.md §10.
 */

export interface HomeworkTileInput {
  /** Contract `urgency` !== 'normal'. */
  urgent: boolean;
  /** `my_state.completion === 'completed'`. */
  completed?: boolean;
  cancelled?: boolean;
}

/** Homework: calm blue document; red deadline tile only when the contract says the task is urgent. */
export function homeworkTileAsset({ urgent, completed = false, cancelled = false }: HomeworkTileInput): ThemedAsset {
  if (cancelled) return assets.tiles.document; // rendered muted by the card
  if (completed) return assets.tiles.completed;
  return urgent ? assets.tiles.deadline : assets.tiles.document;
}

const SUBJECT_RULES: ReadonlyArray<readonly [RegExp, keyof typeof assets.subjects]> = [
  [/(матем|алгебр|геометр|анализ|статист|вероятн|дискрет|математ)/, 'math'],
  [/(эконом|финанс|бухгалт|учет|учёт|аудит)/, 'economics'],
  [/(истор|культур|философ|полит)/, 'history'],
  [/(англ|english|иностран|немец|француз|язык)/, 'english'],
  [/(маркет|менеджм|реклам|бренд|управлен)/, 'marketing'],
  [/(физкульт|физическ|спорт|физ-ра|физра)/, 'pe'],
];

/**
 * Lesson subject → tile. Presentation only: the API has no subject icon key and none is being added now, so this is a
 * temporary match over the display name. It never affects data, entity type or urgency (e.g. a homework titled «КТ по
 * истории» stays an ordinary homework; type comes from the contract). `null` → neutral fallback icon.
 */
export function subjectTileAsset(subjectName: string): ThemedAsset | null {
  const name = subjectName.toLowerCase();
  for (const [re, key] of SUBJECT_RULES) if (re.test(name)) return assets.subjects[key];
  return null;
}

/** Source states. «Telegram» uses the brand mark (ui/icons); an unavailable source uses the broken-link tile. */
export const sourceUnavailableTile: ThemedAsset = assets.tiles.link;

/** Reserved (not used by Slice 1 data): attachment kinds and announcements. */
export const reservedTiles = {
  pdf: assets.tiles.pdf,
  video: assets.tiles.video,
  image: assets.tiles.image,
  link: assets.tiles.link,
  announcement: assets.tiles.announcement,
  update: assets.tiles.update,
  /** Graduation cap: only for a control-point type that comes from the contract (never from the title). */
  exam: assets.tiles.exam,
} as const;

/** Empty-state illustrations. */
export const emptyIllustrations = {
  noTasks: assets.emptyTasks,
  noLessons: assets.calendar,
  preparing: assets.folder,
} as const;
