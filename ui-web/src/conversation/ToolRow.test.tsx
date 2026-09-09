import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $activeTabId, $navigation, $tabs, resetSidebar } from '../sidebar-right/store.ts'
import type { ToolNode } from '../state/transcript.ts'
import { ToolRow } from './ToolRow.tsx'

function tool(over: Partial<ToolNode> = {}): ToolNode {
  return {
    args: {},
    id: 'n1',
    kind: 'tool',
    name: 'read_file',
    startedAt: 0,
    state: 'done',
    toolId: 't1',
    ...over,
  }
}

beforeEach(resetSidebar)

afterEach(() => {
  cleanup()
  resetSidebar()
})

describe('a file tool row', () => {
  it('opens the file it names in the sidebar', () => {
    render(
      <ToolRow node={tool({ args: { path: '/repo/src/app.ts' } })} workspace="/repo" />,
    )

    fireEvent.click(screen.getByText('src/app.ts'))

    expect($tabs.get().map(entry => entry.id)).toContain('text:/repo/src/app.ts')
    expect($activeTabId.get()).toBe('text:/repo/src/app.ts')
  })

  it('lands where the agent was reading', () => {
    render(
      <ToolRow
        node={tool({ args: { offset: 120, path: '/repo/src/app.ts' } })}
        workspace="/repo"
      />,
    )

    fireEvent.click(screen.getByText('src/app.ts'))

    expect($navigation.get()['text:/repo/src/app.ts']).toEqual({ line: 120, revision: 1 })
  })

  it('resolves a path the tool reported relative to the workspace', () => {
    render(<ToolRow node={tool({ args: { path: 'src/app.ts' } })} workspace="/repo" />)

    fireEvent.click(screen.getByText('src/app.ts'))

    expect($tabs.get().map(entry => entry.id)).toContain('text:/repo/src/app.ts')
  })

  it('does not toggle the row open on the way to the file', () => {
    // The row's disclosure is the outer target; opening a file is a different
    // intent and must not also expand the card.
    const node = tool({
      args: { path: '/repo/src/app.ts' },
      result: { content: '1\tconst a = 1' },
    })
    const { container } = render(<ToolRow node={node} workspace="/repo" />)

    fireEvent.click(screen.getByText('src/app.ts'))

    expect(container.textContent).not.toContain('const a = 1')
  })

  it('leaves a non-file row summary as plain text', () => {
    render(<ToolRow node={tool({ args: { command: 'ls -la' }, name: 'terminal' })} />)

    expect(screen.getByText('ls -la').tagName).not.toBe('BUTTON')
  })
})
