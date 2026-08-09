import { expect, test } from './test'

import {
  PACKAGED_BINARY_PATH,
  type PackagedAppFixture,
  packagedBinaryExists,
  setupPackagedApp,
} from './fixtures'
import { expectVisualSnapshot } from './visual-snapshot'

/**
 * E2E smoke tests for the packaged ClawCodex desktop app.
 *
 * Launches the real packaged Electron binary (produced by `npm run pack` →
 * `electron-builder --dir`) with BOOT_FAKE=1 and full sandbox isolation
 * (credential stripping, isolated CLAWCODEX_CONFIG_DIR + userData, unique app name).
 *
 * Skips if the packaged binary doesn't exist — run `npm run pack` first.
 */

let fixture: PackagedAppFixture | null = null

test.beforeAll(async () => {
  test.skip(
    !packagedBinaryExists(),
    `Built app binary not found: ${PACKAGED_BINARY_PATH}. Run 'npm run pack' first.`,
  )

  fixture = await setupPackagedApp()
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('window opens with the ClawCodex title', async () => {
  const title = await fixture!.page.title()
  expect(title).toContain('ClawCodex')
})

test('renderer loads and shows DOM content', async () => {
  const page = fixture!.page
  await page.waitForSelector('#root', { state: 'attached', timeout: 30_000 })
  const childCount = await page.locator('#root > *').count()
  expect(childCount).toBeGreaterThan(0)
})

test('boot progress overlay fades out or shows error state', async () => {
  const page = fixture!.page
  await page.waitForFunction(
    () => {
      const root = document.getElementById('root')

      if (!root) {
        return false
      }

      const text = root.textContent ?? ''

      // Error path: boot failure overlay renders an error message.
      if (text.includes('error') || text.includes('Error') || text.includes('failed')) {
        return true
      }

      const lower = text.toLowerCase()

      // Success path A: the isolated sandbox config has no providers, so a
      // packaged boot legitimately terminates at the first-run/onboarding
      // gate — an interactive state whose status widget still says
      // "Starting ClawCodex… / Waiting for first-run setup choice". The
      // onboarding copy being on screen means boot is done.
      if (lower.includes('connect a model provider')) {
        return true
      }

      // Success path B: overlay disappears and the app renders. If there's
      // no "boot" / "starting" / "installing" text visible, boot has
      // completed to the main UI.
      const bootIndicators = ['starting', 'resolving', 'spawning', 'waiting', 'installing']

      return !bootIndicators.some((word) => lower.includes(word))
    },
    undefined,
    { timeout: 60_000 },
  )
})

test('can capture a screenshot for the CI artifact', async () => {
  if (!fixture) {
    test.skip(true, 'Previous test failed — no app running')

    return
  }

  // Visual snapshot — won't fail on diff, just logs + generates diff image
  await expectVisualSnapshot(fixture!.page, { name: 'packaged-app-booted', timeout: 10_000, app: fixture!.app })
})
