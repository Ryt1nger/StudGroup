import { defineConfig } from '@hey-api/openapi-ts';

export default defineConfig({
  input: '../contracts/openapi.yaml',
  output: { path: 'src/generated', format: false, lint: false },
  plugins: ['@hey-api/typescript', '@hey-api/sdk', '@hey-api/client-fetch'],
});
