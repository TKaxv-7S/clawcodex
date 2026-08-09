# ClawCodex Desktop

The native desktop app for [ClawCodex](../README.md) — chat with the agent in a
polished native window: streaming responses, live tool activity, side-by-side
previews, a file browser, and settings, no terminal required. Built with
Electron, Vite, and React.

> **Status: working end to end.** The app boots the ClawCodex Python backend
> (`clawcodex serve`) itself and runs real sessions against it. Packaging:
> `npm run dist:mac` (DMG/zip) on macOS and `npm run dist:win:nsis`
> (NSIS installer, unsigned) on Windows; `clawcodex desktop` launches the
> dev app from a checkout on both.

## Layout

- `src/` — the renderer: a React app (chat surface, panes, previews, settings).
- `electron/` — the main process: window/process lifecycle, native capabilities,
  backend boot, and a narrow typed IPC bridge (`preload.ts`).
- `packages/shared/` — `@clawcodex/shared`, types shared between surfaces.
- `e2e/` — Playwright specs driven against a mock backend.
- `scripts/` — build, packaging, and diagnostic tooling.

Engineering conventions live in [`AGENTS.md`](./AGENTS.md); the visual and
interaction contract lives in [`DESIGN.md`](./DESIGN.md).

## Development

```bash
cd ui-desktop
npm ci                 # standalone install — no workspace root required
npm run typecheck      # renderer + electron + e2e tsconfigs
npm run lint
npm test               # vitest: ui + electron projects
```

`npm run dev` (Vite renderer + Electron shell) boots the full app — the main
process spawns `clawcodex serve` from the checkout this repo sits in
(`CLAWCODEX_DESKTOP_CLAWCODEX_ROOT` pins a different backend root). On
Windows, install [Git for Windows](https://git-scm.com/download/win) first —
the backend's shell tool runs commands through Git Bash.

## Testing

- Unit/component tests: `npm test` (vitest projects `ui` and `electron`).
- E2E: `npm run test:e2e` (Playwright against the mock backend) — enabled at
  the end of the port.
