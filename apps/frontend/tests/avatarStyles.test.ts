import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const css = readFileSync('src/shell/demo/DemoHeaderActions.module.css', 'utf8');

describe('profile avatar compatibility', () => {
  for (const selector of ['avatar', 'avatarLarge']) {
    it(`${selector} keeps a solid background independent of advanced colour functions`, () => {
      const rule = css.match(new RegExp(`^\\.${selector} \\{([^}]+)\\}`, 'm'))?.[1];
      expect(rule).toBeDefined();
      expect(rule).toContain('background-color: #5b4dff');
      expect(rule).toContain('background-color: var(--sg-color-primary, #5b4dff)');
      expect(rule).not.toContain('color-mix(');
      expect(rule).not.toMatch(/\bbackground:/);
      expect(rule).toContain('flex-shrink: 0');
    });
  }
});
