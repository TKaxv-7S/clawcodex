/**
 * The nano chip (backend `--nano`, docs/nano.md) on its three surfaces:
 * the composer row, the stats row under it, and the session tab.
 *
 * The rule under every case: the chip is driven by an explicit `true` and
 * nothing else — a backend that never says nano must never grow a badge.
 */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SessionTab } from '../sidebar-right/SessionTab.tsx'
import { $contextUsage, $sessionId, $transcript, $workspace } from '../state/store.ts'
import { emptyTranscript } from '../state/transcript.ts'
import type { TrajectoryStats } from '../state/trajectory.ts'
import { InputBar } from './InputBar.tsx'
import { StatsPills } from './StatsPills.tsx'

afterEach(() => {
  cleanup()
  $transcript.set(emptyTranscript())
  $workspace.set('')
  $sessionId.set(null)
  $contextUsage.set(null)
})

function renderBar(nano?: boolean) {
  render(
    <InputBar
      draft=""
      effort={{ supported: false }}
      models={{}}
      nano={nano}
      onApprovalModeChange={vi.fn()}
      onDraftChange={vi.fn()}
      onEffortChange={vi.fn()}
      onModelChange={vi.fn()}
      onStop={vi.fn()}
      onSubmit={vi.fn()}
      running={false}
      usage={null}
    />,
  )
}

describe('InputBar nano chip', () => {
  it('renders the chip when the session is nano', () => {
    renderBar(true)

    expect(screen.getByText('nano')).toBeTruthy()
  })

  it('renders nothing by default', () => {
    // Absent on older backends must stay absent here — a chip with no flag
    // behind it would claim a mode the session is not in.
    renderBar()

    expect(screen.queryByText('nano')).toBeNull()
  })

  it('is a fact, not a control — no button role', () => {
    renderBar(true)

    const chip = screen.getByText('nano')

    expect(chip.tagName).toBe('SPAN')
    expect(chip.getAttribute('role')).toBeNull()
  })
})

const NO_RUN: TrajectoryStats = {
  cacheHitRatio: null,
  cacheReadTokens: 0,
  cacheWriteTokens: 0,
  inputTokens: 0,
  llmMs: 0,
  outputTokens: 0,
  steps: 0,
  throughput: null,
  toolMs: 0,
  ttftMs: null,
  turns: 0,
  uncachedInputTokens: 0,
}

describe('StatsPills nano chip', () => {
  it('rides the model segment', () => {
    render(<StatsPills model="deepseek-v4-flash" nano provider="deepseek" stats={NO_RUN} />)

    const model = screen.getByText('deepseek:deepseek-v4-flash')
    const chip = screen.getByText('nano')

    // Same segment: whatever narrows the row cannot shed the mode without also
    // shedding the model it describes (the TUI stats-line contract).
    expect(model.contains(chip)).toBe(true)
  })

  it('shows no chip without the flag', () => {
    render(<StatsPills model="deepseek-v4-flash" provider="deepseek" stats={NO_RUN} />)

    expect(screen.queryByText('nano')).toBeNull()
  })

  it('shows no chip with no model to describe', () => {
    // The chip rides the model segment; with nothing to ride it stays off
    // rather than floating as a lone token in an otherwise empty row.
    const { container } = render(<StatsPills nano stats={NO_RUN} />)

    expect(container.firstChild).toBeNull()
  })
})

describe('SessionTab harness row', () => {
  it('names the harness when the session is nano', () => {
    $transcript.set({
      ...emptyTranscript(),
      info: { model: 'deepseek-v4-flash', nano: true, provider: 'deepseek' },
    })

    render(<SessionTab />)

    expect(screen.getByText('Harness')).toBeTruthy()
    expect(screen.getByText('nano')).toBeTruthy()
  })

  it('shows no harness row for a default session', () => {
    // Default mode is not a fact worth a row — and strict === true keeps a
    // backend that never reported the field silent too.
    $transcript.set({
      ...emptyTranscript(),
      info: { model: 'deepseek-v4-flash', provider: 'deepseek' },
    })

    render(<SessionTab />)

    expect(screen.queryByText('Harness')).toBeNull()
  })
})
