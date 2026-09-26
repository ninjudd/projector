// The site's TypeScript is linted with typescript-eslint's strictest type-aware
// sets, strictTypeChecked and stylisticTypeChecked, on top of ESLint's own
// recommended rules, plus a few rules those sets leave out.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['assets/**', 'node_modules/**', 'eslint.config.js'] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  {
    files: ['src/**/*.ts'],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // Conditions are booleans, not whatever happens to be truthy.
      '@typescript-eslint/strict-boolean-expressions': 'error',
      '@typescript-eslint/switch-exhaustiveness-check': 'error',
      '@typescript-eslint/explicit-function-return-type': 'error',
      '@typescript-eslint/prefer-readonly': 'error',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-console': 'error',
      'no-var': 'error',
      'prefer-const': 'error',
      curly: ['error', 'multi-line'],
    },
  },
);
