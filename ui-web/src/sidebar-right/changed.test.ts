import { describe, expect, it } from 'vitest'

import type { ToolNode } from '../state/transcript.ts'
import { changedSince } from './changed.ts'

function tool(over: Partial<ToolNode> = {}): ToolNode {
  return {
    args: { path: '/repo/a.ts' },
    endedAt: 200,
    id: 'n1',
    kind: 'tool',
    name: 'write_file',
    startedAt: 100,
    state: 'done',
    toolId: 't1',
    ...over,
  }
}

describe('changedSince', () => {
  it('reports a write that landed after the page was read', () => {
    expect(changedSince([tool()], '/repo/a.ts', 100)).toBe(true)
  })

  it('ignores a write that landed before it', () => {
    expect(changedSince([tool({ endedAt: 50 })], '/repo/a.ts', 100)).toBe(false)
  })

  it('ignores a write to another file', () => {
    expect(changedSince([tool({ args: { path: '/repo/b.ts' } })], '/repo/a.ts', 100)).toBe(false)
  })

  it('resolves a relative path against the workspace before comparing', () => {
    expect(changedSince([tool({ args: { path: 'a.ts' } })], '/repo/a.ts', 100, '/repo')).toBe(true)
  })

  it('ignores a read: reading a file does not change it', () => {
    expect(changedSince([tool({ name: 'read_file' })], '/repo/a.ts', 100)).toBe(false)
  })

  it('ignores a write that did not land', () => {
    expect(changedSince([tool({ state: 'error' })], '/repo/a.ts', 100)).toBe(false)
  })

  it('says nothing about a rehydrated node with no end time', () => {
    // A resumed session would otherwise announce a change on every file it
    // ever wrote.
    expect(changedSince([tool({ endedAt: undefined })], '/repo/a.ts', 100)).toBe(false)
  })

  it('says nothing before the first page has landed', () => {
    expect(changedSince([tool()], '/repo/a.ts', 0)).toBe(false)
  })
})
