import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
  },
  // vite.config.js 와 tools/*.mjs 는 Node 에서 돈다.
  {
    files: ['vite.config.js', 'tools/**/*.{js,mjs}'],
    languageOptions: { globals: { ...globals.node } },
  },
  // 테스트는 vitest globals 와 setup.js 가 심는 global.MockWebSocket 을 쓴다.
  {
    files: ['**/*.test.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: {
      globals: { ...globals.node, ...globals.vitest, MockWebSocket: 'readonly' },
    },
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
