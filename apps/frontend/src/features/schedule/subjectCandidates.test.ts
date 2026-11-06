import { describe, expect, it } from 'vitest';
import { subjectCandidates } from './subjectCandidates';

describe('subject candidates', () => {
  it('finds existing full names for clipped prefixes and middle fragments', () => {
    const names = ['Линейная алгебра и аналитическая геометрия', 'Математический анализ'];
    expect(subjectCandidates('Линейная алгебра и...', names)).toEqual([names[0]]);
    expect(subjectCandidates('Математиче... анализ', names)).toEqual([names[1]]);
  });

  it('does not guess when the name has no clipping marker', () => {
    expect(subjectCandidates('Математический анализ', ['Математический анализ'])).toEqual([]);
  });
});
