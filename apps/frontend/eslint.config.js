import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'playwright-report', 'test-results', 'screenshots'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, reactHooks.configs.flat.recommended],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
    plugins: { 'react-refresh': reactRefresh },
    rules: {
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      '@typescript-eslint/consistent-type-imports': 'error',
      'no-restricted-globals': ['error', { name: 'localStorage', message: 'No persistent client storage of learning data (owner decision).' }, { name: 'sessionStorage', message: 'No persistent client storage of learning data (owner decision).' }],
      'no-restricted-properties': ['error',
        { object: 'window', property: 'Telegram', message: 'Access Telegram only through src/telegram/adapter.' },
        { object: 'window', property: 'localStorage', message: 'No persistent client storage.' },
        { object: 'window', property: 'sessionStorage', message: 'No persistent client storage.' },
        { object: 'window', property: 'indexedDB', message: 'No persistent client storage.' },
      ],
    },
  },
  {
    // The only module allowed to touch the Telegram global.
    files: ['src/telegram/realAdapter.ts', 'src/telegram/globals.d.ts'],
    rules: { 'no-restricted-properties': 'off' },
  },
  { files: ['scripts/**', 'e2e/**', 'tests/**', '*.config.*'], rules: { 'no-restricted-globals': 'off' } },
);
