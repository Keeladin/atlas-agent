import { execFileSync } from 'node:child_process'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

const buildRevision = (() => {
  try { return execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim() } catch { return 'unknown' }
})()

export default defineConfig({
  define: { 'import.meta.env.VITE_ATLAS_BUILD_SHA': JSON.stringify(buildRevision) },
  test: { environment: 'jsdom', setupFiles: './src/test/setup.ts', restoreMocks: true, css: true },
  plugins: [react(), VitePWA({ manifest: false, injectRegister: 'script', strategies: 'injectManifest', srcDir: 'src/pwa', filename: 'sw.ts', injectManifest: { globPatterns: ['**/*.{js,css,html,svg,png,ico,webmanifest}'] } })],
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8080', changeOrigin: false } } },
})
