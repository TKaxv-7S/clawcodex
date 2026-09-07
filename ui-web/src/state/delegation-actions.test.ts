import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import * as actions from './actions.ts'
import { setGatewayClient } from './actions.ts'
import { $delegation, $notice, $sessionId } from './store.ts'

// The panel's own tests mock this module wholesale, so the request shapes and
// the failure behaviour below are only covered here. Injected through the same
// seam actions.test.ts uses, so the real functions run.
const request = vi.fn()

beforeEach(() => {
  request.mockReset()
  setGatewayClient({ request } as unknown as GatewayClient)
  $sessionId.set('s1')
  $delegation.set(null)
  $notice.set({ text: '', tone: 'info' })
})

afterEach(() => {
  setGatewayClient(null)
  $sessionId.set(null)
  $delegation.set(null)
})

describe('fetchDelegationStatus', () => {
  it('sends the session id and stores the snapshot', async () => {
    const snapshot = { active: [{ subagent_id: 'a1' }], max_concurrent_children: 32 }

    request.mockResolvedValue(snapshot)
    await actions.fetchDelegationStatus()

    expect(request).toHaveBeenCalledWith('delegation.status', { session_id: 's1' })
    expect($delegation.get()).toEqual(snapshot)
  })

  it('keeps the last good snapshot when a poll fails', async () => {
    // A dropped poll during a reconnect is not evidence the agents stopped;
    // blanking the list would say it was.
    const good = { active: [{ subagent_id: 'a1' }] }

    request.mockResolvedValueOnce(good)
    await actions.fetchDelegationStatus()

    request.mockRejectedValueOnce(new Error('socket closed'))
    await actions.fetchDelegationStatus()

    expect($delegation.get()).toEqual(good)
  })

  it('does nothing without a live session', async () => {
    $sessionId.set(null)
    await actions.fetchDelegationStatus()

    expect(request).not.toHaveBeenCalled()
  })
})

describe('setDelegationPaused', () => {
  it('sends the value it wants rather than asking for a flip', async () => {
    request.mockResolvedValue({ paused: true })
    await actions.setDelegationPaused(true)

    expect(request).toHaveBeenCalledWith('delegation.pause', {
      paused: true,
      session_id: 's1',
    })
    expect($delegation.get()?.paused).toBe(true)
  })

  it('reports a failure to the user instead of showing a state it did not reach', async () => {
    request.mockRejectedValue(new Error('nope'))
    await actions.setDelegationPaused(true)

    expect($notice.get().tone).toBe('error')
  })
})

describe('interruptSubagent', () => {
  it('names both the session and the agent', async () => {
    request.mockResolvedValue({ found: true, subagent_id: 'a1' })
    await actions.interruptSubagent('a1')

    expect(request).toHaveBeenCalledWith('subagent.interrupt', {
      session_id: 's1',
      subagent_id: 'a1',
    })
  })

  it('says so when the agent had already finished rather than implying a kill', async () => {
    request.mockResolvedValue({ found: false, subagent_id: 'a1' })
    await actions.interruptSubagent('a1')

    expect($notice.get().text).toMatch(/already finished/i)
  })

  it('refreshes the snapshot afterwards so the row reflects the new state', async () => {
    request.mockResolvedValue({ active: [], found: true })
    await actions.interruptSubagent('a1')

    expect(request.mock.calls.map(c => c[0])).toEqual([
      'subagent.interrupt',
      'delegation.status',
    ])
  })
})
