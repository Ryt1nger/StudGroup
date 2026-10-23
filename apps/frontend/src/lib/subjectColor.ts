import type { CSSProperties } from 'react';

/**
 * Subject colours are a presentation choice: the API has no subject colour, so a stable shade of the
 * current accent hue is derived from the subject name. Same name, same colour, on every screen.
 */
const SHADES: ReadonlyArray<readonly [number, number]> = [
  [0, 0],
  [16, 8],
  [-14, -4],
  [-6, 14],
  [28, 12],
  [-24, 2],
  [10, -8],
  [-30, 10],
];

function hash(name: string): number {
  let value = 0;
  for (const char of name.toLowerCase().trim()) value = (value * 31 + char.charCodeAt(0)) >>> 0;
  return value;
}

/** Style variables for an element tinted by its subject: the colour, its hsl components (for alpha) and a darker ink. */
export function subjectStyle(name: string): CSSProperties {
  const [hue, lightness] = SHADES[hash(name) % SHADES.length]!;
  const hsl = `calc(var(--sg-h) + ${hue}) var(--sg-s) calc(var(--sg-l) + ${lightness}%)`;
  return {
    '--sg-subject': `hsl(${hsl})`,
    '--sg-subject-hsl': hsl,
    '--sg-subject-ink': `calc(var(--sg-h) + ${hue}) var(--sg-s) calc(var(--sg-l) + ${lightness}% - 26%)`,
  } as CSSProperties;
}

/** A CSS colour built from the accent variables, so it follows the chosen accent and theme. */
export function subjectColor(name: string): string {
  const [hue, lightness] = SHADES[hash(name) % SHADES.length]!;
  return `hsl(calc(var(--sg-h) + ${hue}) var(--sg-s) calc(var(--sg-l) + ${lightness}%))`;
}

/** One capital letter for the subject chip. */
export function subjectGlyph(name: string): string {
  const letter = name.trim().charAt(0);
  return letter ? letter.toLocaleUpperCase('ru') : '•';
}
