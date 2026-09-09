import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import type { FilePage } from '../gateway/protocol.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $transcript, $workspace } from '../state/store.ts'
import { emptyTranscript, type ToolNode } from '../state/transcript.ts'
import { $textTabs, resetTextTabs } from './text-store.ts'
import { openFile, resetSidebar } from './store.ts'
import { TextPreview } from './TextPreview.tsx'

const request = vi.fn()

function page(over: Partial<FilePage> = {}): { ok: true } & FilePage {
  return {
    absolute_path: '/repo/a.ts',
    bytes: 40,
    eof: true,
    lines: 3,
    offset: 1,
    ok: true,
    text: 'one\ntwo\nthree',
    version: 'v1',
    ...over,
  }
}

const TAB = 'text:/repo/a.ts'

function write(over: Partial<ToolNode> = {}): ToolNode {
  return {
    args: { path: '/repo/a.ts' },
    endedAt: 5_000,
    id: 'n1',
    kind: 'tool',
    name: 'write_file',
    startedAt: 4_000,
    state: 'done',
    toolId: 't1',
    ...over,
  }
}

beforeEach(() => {
  request.mockReset()
  request.mockResolvedValue(page())
  setGatewayClient({ request } as unknown as GatewayClient)
  $workspace.set('/repo')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
})

afterEach(() => {
  cleanup()
  setGatewayClient(null)
  $workspace.set('')
  $transcript.set(emptyTranscript())
  resetSidebar()
  resetTextTabs()
})

describe('TextPreview', () => {
  it('reads the first page on its first mount', async () => {
    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
    expect(request).toHaveBeenCalledTimes(1)
  })

  it('reads nothing when it comes back to a tab that already has pages', async () => {
    const { unmount } = render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
    unmount()
    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    expect(request).toHaveBeenCalledTimes(1)
  })

  it('shows one row per line, so an empty line is one line tall', async () => {
    request.mockResolvedValue(page({ lines: 3, text: 'one\n\nthree' }))

    const { container } = render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    await waitFor(() => {
      expect(container.querySelectorAll('[data-preview-line]')).toHaveLength(3)
    })
  })

  it('offers the next page until the file ends', async () => {
    request.mockResolvedValueOnce(page({ eof: false }))

    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    const more = await screen.findByText('Load more')

    request.mockResolvedValueOnce(page({ lines: 1, offset: 4, text: 'four' }))
    fireEvent.click(more)

    await waitFor(() => {
      expect(screen.getByText('four')).toBeTruthy()
    })
    expect(request).toHaveBeenLastCalledWith('fs.read_file', {
      cwd: '/repo',
      offset: 4,
      path: '/repo/a.ts',
    })
    expect(screen.queryByText('Load more')).toBeNull()
  })

  it('says why a page is missing and offers the same page again', async () => {
    request.mockResolvedValueOnce({
      error: { code: 'workspace-file/not-found', message: 'gone' },
      ok: false,
    })

    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    expect(await screen.findByText('That file is gone. It may have been moved or deleted.'))
      .toBeTruthy()

    request.mockResolvedValueOnce(page())
    fireEvent.click(screen.getByText('Retry'))

    await waitFor(() => {
      expect(screen.getByText('three')).toBeTruthy()
    })
  })

  it('wraps by default and unwraps on the toggle', async () => {
    const { container } = render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    await screen.findByText('three')

    const toggle = screen.getByLabelText('Wrap lines')

    expect(toggle.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('[data-preview-body]')?.className).toMatch(/wrap/)

    fireEvent.click(toggle)

    expect($textTabs.get()[TAB]?.wrap).toBe(false)
  })

  it('announces a file the agent wrote after the page was read, without applying it', async () => {
    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)
    await screen.findByText('three')

    $transcript.set({ ...emptyTranscript(), nodes: [write({ endedAt: Date.now() + 1000 })] })

    const notice = await screen.findByText('The file has changed; this is the older text.')

    // Announced, not applied: the pages under the reader are untouched until
    // the click.
    expect(notice).toBeTruthy()
    expect(request).toHaveBeenCalledTimes(1)
    expect(screen.getByText('three')).toBeTruthy()

    request.mockResolvedValueOnce(page({ text: 'after', lines: 1, version: 'v2' }))
    fireEvent.click(screen.getByText('Reload'))

    await waitFor(() => {
      expect(screen.getByText('after')).toBeTruthy()
    })
  })

  it('says nothing about a write that happened before the read', async () => {
    $transcript.set({ ...emptyTranscript(), nodes: [write({ endedAt: 1 })] })

    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)
    await screen.findByText('three')

    expect(screen.queryByText('The file has changed; this is the older text.')).toBeNull()
  })

  it('marks the line a navigation asked for, once', async () => {
    openFile('/repo/a.ts', 2)

    const { container } = render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    await waitFor(() => {
      expect(container.querySelector('[data-preview-line="2"]')?.className).toMatch(/lineTarget/)
    })
    await waitFor(() => {
      expect($textTabs.get()[TAB]?.answered).toBe(1)
    })
  })

  it('walks pages until they reach the line it was sent to', async () => {
    // Pages load in order; there is no seek, so a deep line reads forward.
    request.mockResolvedValueOnce(page({ eof: false }))
    openFile('/repo/a.ts', 5)

    render(<TextPreview path="/repo/a.ts" tabId={TAB} />)

    request.mockResolvedValueOnce(page({ lines: 2, offset: 4, text: 'four\nfive' }))

    await waitFor(() => {
      expect(screen.getByText('five')).toBeTruthy()
    })
    expect(request).toHaveBeenCalledTimes(2)
  })
})
