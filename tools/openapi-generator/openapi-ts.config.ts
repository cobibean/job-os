import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: '../../packages/contracts/openapi.json',
  output: {
    clean: true,
    importFileExtension: '.js',
    path: '../../packages/contracts/src/generated'
  },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk']
})
