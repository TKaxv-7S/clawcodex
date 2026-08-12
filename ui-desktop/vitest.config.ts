import type { TestProjectConfiguration } from 'vitest/config';
import { defineConfig } from 'vitest/config'

const reactUi: TestProjectConfiguration = {
  extends: './vite.config.ts',
  test: {
    name: 'ui',
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    globals: true,
    // The first test in each file pays jsdom env init + full module transform,
    // which can exceed vitest's 5000ms default under CI/load. Full-suite runs
    // on Windows dev boxes starve cold starts past 15s (filesystem-heavy
    // transforms), and a test killed mid-import leaks its late render into the
    // next test's DOM ("Found multiple elements" cascades). 30s covers the
    // worst observed cold start without masking genuinely hung tests.
    testTimeout: 30_000
  }
}

const electronNative: TestProjectConfiguration = {
  test: {
    name: 'electron',
    environment: 'node',
    include: ['electron/**/*.test.ts', 'scripts/**.test.{ts,mjs}'],
    // Same measure as the ui project above, and this project needs it more:
    // these tests shell out for real (git clone/worktree fixtures, spawned
    // node helpers), and Windows pays ~10x POSIX for process spawn plus a
    // Defender scan per spawned exe — a 2-core windows-latest runner blows
    // the 5s default on work that takes well under a second locally. A
    // genuinely hung test still dies, at 30s.
    testTimeout: 30_000
  }
}

export default defineConfig({
  test: {
    projects: [reactUi, electronNative]
  }
})
