import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  base: './',
  plugins: [react()],
  root: 'src/renderer',
  build: {
    outDir: '../../dist/renderer',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL('./src/renderer/index.html', import.meta.url)),
        print: fileURLToPath(new URL('./src/renderer/print.html', import.meta.url)),
        docxWorker: fileURLToPath(new URL('./src/renderer/docx-worker.html', import.meta.url))
      }
    }
  }
})
