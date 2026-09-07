import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DelegationStatusResult } from '../gateway/protocol.ts'
import { $delegation, $sessionId } from '../state/store.ts'
import { AgentsPanel } from './AgentsPanel.tsx'

// The panel drives the gateway through these; the transport has its own tests.
const actions = vi.hoisted(() => ({
  fetchDelegationStatus: vi.fn(async () => {}),
  interruptSubagent: vi.fn(async () => {}),
  setDelegationPaused: vi.fn(async () => {}),
}))

vi.mock('../state/actions.ts', () => actions)

const agent = (over: Partial<NonNullable<DelegationStatusResult['active']>[number]> = {}) => ({
  depth: 0,
  goal: 'audit the store',
  model: 'claude-opus-5',
  parent_id: null,
  started_at: 1_700_000_000,
  status: 'running',
  subagent_id: 'a1',
  tool_count: 4,
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  $sessionId.set('s1')
  $delegation.set(null)
})

afterEach(() => {
  cleanup()
  $sessionId.set(null)
  $delegation.set(null)
})

describe('AgentsPanel', () => {
  it('distinguishes "not fetched yet" from "no agents running"', () => {
    render(<AgentsPanel />)
    expect(screen.getByText('Loading agents…')).toBeTruthy()

    cleanup()
    $delegation.set({ active: [] })
    render(<AgentsPanel />)
    expect(screen.getByText('No agents running.')).toBeTruthy()
  })

  it('lists a live agent with its model, tool count and status', () => {
    $delegation.set({ active: [agent()], max_concurrent_children: 10 })
    render(<AgentsPanel />)

    expect(screen.getByText('audit the store')).toBeTruthy()
    expect(screen.getByText('running')).toBeTruthy()
    expect(screen.getByText(/claude-opus-5/)).toBeTruthy()
    expect(screen.getByText(/4 tools/)).toBeTruthy()
    expect(screen.getByText('1 running of 10')).toBeTruthy()
  })

  it('omits the cap rather than claiming a cap of zero when the backend sends none', () => {
    // "0 of 0" would read as a session that can never delegate.
    $delegation.set({ active: [agent()] })
    render(<AgentsPanel />)

    expect(screen.getByText('1 running')).toBeTruthy()
  })

  it('orders shallowest first, then oldest first', () => {
    $delegation.set({
      active: [
        agent({ depth: 1, goal: 'child', started_at: 1_700_000_500, subagent_id: 'c' }),
        agent({ depth: 0, goal: 'later root', started_at: 1_700_000_300, subagent_id: 'b' }),
        agent({ depth: 0, goal: 'earlier root', started_at: 1_700_000_100, subagent_id: 'a' }),
      ],
    })
    render(<AgentsPanel />)

    const goals = screen.getAllByRole('listitem').map(li => li.textContent ?? '')
    expect(goals[0]).toContain('earlier root')
    expect(goals[1]).toContain('later root')
    expect(goals[2]).toContain('child')
  })

  it('indents by nesting depth so a child reads under its parent', () => {
    $delegation.set({ active: [agent({ depth: 2 })] })
    render(<AgentsPanel />)

    expect(screen.getByRole('listitem').style.paddingLeft).toBe('44px')
  })

  it('interrupts the agent whose Stop button was pressed', () => {
    $delegation.set({ active: [agent({ subagent_id: 'a1' }), agent({ subagent_id: 'a2' })] })
    render(<AgentsPanel />)

    const stops = screen.getAllByRole('button', { name: 'Stop' })

    expect(stops).toHaveLength(2)
    // The second row's button must target the second agent, not the first —
    // an index/id mismatch would silently kill the wrong one.
    stops[1]?.click()
    expect(actions.interruptSubagent).toHaveBeenCalledWith('a2')
  })

  it('disables Stop on an agent that is already stopping', () => {
    // The interrupt already fired; the slot is held until the worker exits, so
    // a second press would do nothing.
    $delegation.set({ active: [agent({ status: 'interrupted' })] })
    render(<AgentsPanel />)

    const stop = screen.getByRole('button', { name: 'Stop' }) as HTMLButtonElement
    expect(stop.disabled).toBe(true)
    expect(screen.getByText('interrupted')).toBeTruthy()
  })

  it('sends the pause state it wants rather than a bare toggle', () => {
    $delegation.set({ active: [], paused: false })
    render(<AgentsPanel />)

    screen.getByRole('button', { name: 'Pause spawning' }).click()
    expect(actions.setDelegationPaused).toHaveBeenCalledWith(true)
  })

  it('reflects a paused session and offers to resume it', () => {
    $delegation.set({ active: [], paused: true })
    render(<AgentsPanel />)

    const button = screen.getByRole('button', { name: 'Spawning paused' })
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('No agents running. New ones are paused.')).toBeTruthy()

    button.click()
    expect(actions.setDelegationPaused).toHaveBeenCalledWith(false)
  })

  it('fetches on mount when a session is live, and not before one exists', () => {
    render(<AgentsPanel />)
    expect(actions.fetchDelegationStatus).toHaveBeenCalledTimes(1)

    cleanup()
    vi.clearAllMocks()
    $sessionId.set(null)
    render(<AgentsPanel />)
    expect(actions.fetchDelegationStatus).not.toHaveBeenCalled()
  })

  it('falls back to a placeholder for an agent with no goal or model', () => {
    $delegation.set({ active: [agent({ goal: '', model: null })] })
    render(<AgentsPanel />)

    expect(screen.getByText('subagent')).toBeTruthy()
    expect(screen.getByText(/default model/)).toBeTruthy()
  })

  it('shows a dash instead of a bogus age when the backend sent no start time', () => {
    $delegation.set({ active: [agent({ started_at: undefined })] })
    render(<AgentsPanel />)

    expect(screen.getByText(/· —$/)).toBeTruthy()
  })
})
