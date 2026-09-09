import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { TrajectoryStats } from '../state/trajectory.ts'
import { cacheHitPercent, formatSpeed, StatsPills } from './StatsPills.tsx'

afterEach(cleanup)

const EMPTY: TrajectoryStats = {
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

const RAN: TrajectoryStats = {
  cacheHitRatio: 0.55,
  cacheReadTokens: 16_555,
  cacheWriteTokens: 1200,
  inputTokens: 30_100,
  llmMs: 7900,
  outputTokens: 640,
  steps: 2,
  throughput: 156,
  toolMs: 800,
  ttftMs: 1900,
  turns: 1,
  uncachedInputTokens: 13_545,
}

const text = (): string => document.body.textContent ?? ''

describe('cacheHitPercent', () => {
  it('never rounds a partial hit up to a full one', () => {
    // "100% cached" has to mean every prompt token came from cache; a 99.7%
    // run reading 100% is the one a person would act on differently.
    expect(cacheHitPercent(0.997)).toBe('99.7')
    expect(cacheHitPercent(0.9997)).toBe('99.97')
    expect(cacheHitPercent(0.99997)).toBe('99.997')
  })

  it('keeps a whole number whole, and a full hit at 100', () => {
    expect(cacheHitPercent(0.55)).toBe('55')
    expect(cacheHitPercent(1)).toBe('100')
  })
})

describe('formatSpeed', () => {
  it('keeps the digit that matters below 10', () => {
    // "0 tok/s" would report a stalled run that is merely slow.
    expect(formatSpeed(0.4)).toBe('0.4')
    expect(formatSpeed(3.42)).toBe('3.4')
  })

  it('drops it above 10, where it carries nothing', () => {
    expect(formatSpeed(156.4)).toBe('156')
  })
})

describe('StatsPills', () => {
  it('renders nothing before a turn with no model to name', () => {
    // A row holding nothing but separators is worse than no row.
    const { container } = render(<StatsPills stats={EMPTY} />)

    expect(container.firstChild).toBeNull()
  })

  it('names the model before any figures exist', () => {
    // A fresh session still says what it is about to run on.
    render(<StatsPills model="deepseek-v4-pro" provider="deepseek" stats={EMPTY} />)

    expect(screen.getByText('deepseek:deepseek-v4-pro')).toBeTruthy()
    expect(text()).not.toMatch(/turn|tok/)
  })

  it('shows the model alone when the provider is unknown', () => {
    render(<StatsPills model="deepseek-v4-pro" stats={EMPTY} />)

    expect(screen.getByText('deepseek-v4-pro')).toBeTruthy()
  })

  it('reads as counts and speed, then what it cost', () => {
    render(<StatsPills model="deepseek-v4-pro" provider="deepseek" stats={RAN} />)

    const shown = text()

    expect(shown).toContain('1 turn')
    expect(shown).toContain('2 steps')
    expect(shown).toContain('156 tok/s')
    // 31.9K, not 32K: the digit is the point of the K range.
    expect(shown).toContain('31.9K tok')
    expect(shown).toContain('55% cached')
  })

  it('says "1 turn" and "2 turns"', () => {
    const { rerender } = render(<StatsPills stats={RAN} />)
    expect(text()).toContain('1 turn')

    rerender(<StatsPills stats={{ ...RAN, steps: 1, turns: 2 }} />)
    expect(text()).toContain('2 turns')
    expect(text()).toContain('1 step')
  })

  it('opens the timings behind the gauge pill', () => {
    // The figures the strip used to carry resident are one click away, and
    // model time and tool time stay apart: "8.7s" says nothing actionable,
    // "7.9s of model, 0.8s of tools" says which half to go look at.
    render(<StatsPills stats={RAN} />)

    fireEvent.click(screen.getByRole('button', { name: '1 turn · 2 steps · 156 tok/s' }))

    const rows = document.querySelector('[data-session-stats-time]')

    expect(rows?.textContent).toContain('Model time')
    expect(rows?.textContent).toContain('7.90 s')
    expect(rows?.textContent).toContain('Tool time')
    expect(rows?.textContent).toContain('800 ms')
    expect(rows?.textContent).toContain('TTFT avg')
    expect(rows?.textContent).toContain('1.90 s')
  })

  it('opens the exact counts behind the usage pill', () => {
    // The reading on the pill is compact; the dialog is the only place the
    // real numbers appear, which is the whole point of having one.
    render(<StatsPills stats={RAN} />)

    fireEvent.click(screen.getByRole('button', { name: '31,940 tok · 55% cached' }))

    const rows = document.querySelector('[data-session-stats-usage]')

    expect(rows?.textContent).toContain('13,545 tok')
    expect(rows?.textContent).toContain('16,555 tok')
    expect(rows?.textContent).toContain('1,200 tok')
    expect(rows?.textContent).toContain('640 tok')
  })

  it('holds one open dialog at a time', () => {
    render(<StatsPills stats={RAN} />)

    fireEvent.click(screen.getByRole('button', { name: '1 turn · 2 steps · 156 tok/s' }))
    expect(document.querySelector('[data-session-stats-time]')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '31,940 tok · 55% cached' }))

    expect(document.querySelector('[data-session-stats-time]')).toBeNull()
    expect(document.querySelector('[data-session-stats-usage]')).not.toBeNull()
  })

  it('closes an open dialog on Escape', () => {
    render(<StatsPills stats={RAN} />)

    fireEvent.click(screen.getByRole('button', { name: '1 turn · 2 steps · 156 tok/s' }))
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(document.querySelector('[data-session-stats-time]')).toBeNull()
  })

  it('leaves an untimed run as a plain reading rather than an empty dialog', () => {
    // A replayed session records no client-side timings, so the gauge has
    // nothing to open — and a button that opens nothing is worse than text.
    const replayed = { ...RAN, llmMs: 0, throughput: null, toolMs: 0, ttftMs: null }

    render(<StatsPills stats={replayed} />)

    expect(screen.queryByRole('button', { name: /1 turn/ })).toBeNull()
    expect(text()).toContain('1 turn')
  })

  it('omits figures that were never measured instead of printing zeros', () => {
    render(<StatsPills stats={{ ...RAN, cacheHitRatio: null, throughput: null, ttftMs: null }} />)

    fireEvent.click(screen.getByRole('button', { name: '1 turn · 2 steps' }))

    const shown = text()

    expect(shown).not.toContain('TTFT')
    expect(shown).not.toContain('tok/s')
    expect(shown).not.toContain('cached')
    expect(shown).toContain('1 turn')
  })

  it('drops the usage pill for a run that was never billed', () => {
    // A rehydrated ledger has real timings but no usage; zeros here would read
    // as "this run was free".
    const untokened = {
      ...RAN,
      cacheReadTokens: 0,
      cacheWriteTokens: 0,
      outputTokens: 0,
      uncachedInputTokens: 0,
    }

    render(<StatsPills stats={untokened} />)

    fireEvent.click(screen.getByRole('button', { name: /1 turn/ }))

    expect(document.querySelector('[data-session-stats-usage]')).toBeNull()
    expect(screen.queryByRole('button', { name: /tok$|cached/ })).toBeNull()
    expect(text()).toContain('2 steps')
  })
})
