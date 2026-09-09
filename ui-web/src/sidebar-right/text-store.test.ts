import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { FilePage } from '../gateway/protocol.ts'
import type { GatewayClient } from '../gateway/client.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $sessionId, $workspace } from '../state/store.ts'
import {
  $textTabs,
  applyPage,
  emptyTextTab,
  lastLineLoaded,
  linesOf,
  loadedPages,
  loadPage,
  markAnswered,
  reloadPages,
  resetTextTabs,
  setScroll,
  toggleWrap,
} from './text-store.ts'

const request = vi.fn()

function page(over: Partial<FilePage> = {}): { ok: true } & FilePage {
  return {
    absolute_path: '/repo/a.ts',
    bytes: 120,
    eof: true,
    lines: 2,
    offset: 1,
    ok: true,
    text: 'one\ntwo',
    version: 'v1',
    ...over,
  }
}

beforeEach(() => {
  request.mockReset()
  setGatewayClient({ request } as unknown as GatewayClient)
  $sessionId.set('s1')
  $workspace.set('/repo')
  resetTextTabs()
})

afterEach(() => {
  setGatewayClient(null)
  $sessionId.set(null)
  $workspace.set('')
  resetTextTabs()
})

describe('page arithmetic', () => {
  it('tells one empty line apart from a page past the end', () => {
    expect(linesOf({ lines: 1, text: '' })).toEqual([''])
    expect(linesOf({ lines: 0, text: '' })).toEqual([])
  })

  it('orders pages by the line they start at', () => {
    const pages = loadedPages({ 5: { lines: 2, text: 'e\nf' }, 1: { lines: 4, text: 'a\nb\nc\nd' } })

    expect(pages.map(entry => entry.offset)).toEqual([1, 5])
  })

  it('reads the last loaded line from the counts, not from the text', () => {
    expect(lastLineLoaded([])).toBe(0)
    expect(lastLineLoaded([{ lines: 4, offset: 1 }, { lines: 2, offset: 5 }])).toBe(6)
  })
})

describe('applyPage', () => {
  it('appends a later page of the same version', () => {
    const first = applyPage(emptyTextTab('/repo/a.ts'), 1, page({ eof: false })).state
    const second = applyPage(first, 3, page({ lines: 1, offset: 3, text: 'three' }))

    expect(second.restart).toBe(false)
    expect(Object.keys(second.state.pages)).toEqual(['1', '3'])
    expect(second.state.eof).toBe(true)
  })

  it('lets a first page replace everything read before it', () => {
    const first = applyPage(emptyTextTab('/repo/a.ts'), 1, page({ eof: false })).state
    const walked = applyPage(first, 3, page({ offset: 3 })).state
    const reloaded = applyPage(walked, 1, page({ text: 'new', version: 'v2' }))

    expect(Object.keys(reloaded.state.pages)).toEqual(['1'])
    expect(reloaded.state.version).toBe('v2')
  })

  it('refuses to stitch two versions and asks for a restart instead', () => {
    // Two versions shown together would read as one file that never existed.
    const first = applyPage(emptyTextTab('/repo/a.ts'), 1, page({ eof: false })).state
    const moved = applyPage(first, 3, page({ offset: 3, version: 'v2' }))

    expect(moved.restart).toBe(true)
    expect(moved.state.pages).toEqual({})
    expect(moved.state.version).toBe('')
  })
})

describe('loadPage', () => {
  it('seeds a bucket and reads the first page', async () => {
    request.mockResolvedValue(page())

    await loadPage('t1', '/repo/a.ts', 1)

    expect(request).toHaveBeenCalledWith('fs.read_file', {
      offset: 1,
      path: '/repo/a.ts',
      session_id: 's1',
    })
    expect($textTabs.get().t1?.pages[1]?.text).toBe('one\ntwo')
    expect($textTabs.get().t1?.eof).toBe(true)
  })

  it('wraps by default: a preview column is narrow', () => {
    expect(emptyTextTab('/repo/a.ts').wrap).toBe(true)
  })

  it('keeps the pages already read when the next one fails', async () => {
    request.mockResolvedValueOnce(page({ eof: false }))
    await loadPage('t1', '/repo/a.ts', 1)

    request.mockResolvedValueOnce({
      error: { code: 'workspace-file/too-large', message: 'nope' },
      ok: false,
    })
    await loadPage('t1', '/repo/a.ts', 3)

    expect($textTabs.get().t1?.pages[1]).toBeDefined()
    expect($textTabs.get().t1?.failure?.code).toBe('workspace-file/too-large')
    expect($textTabs.get().t1?.loading).toBe(false)
  })

  it('reports a rejected request as a failure rather than throwing', async () => {
    request.mockRejectedValue(new Error('socket closed'))

    await loadPage('t1', '/repo/a.ts', 1)

    expect($textTabs.get().t1?.failure?.code).toBe('workspace-file/unavailable')
    expect($textTabs.get().t1?.failure?.message).toBe('socket closed')
  })

  it('does not start a second read while one is in flight', async () => {
    request.mockResolvedValue(page({ eof: false }))
    await loadPage('t1', '/repo/a.ts', 1)

    let release = (): void => {}

    request.mockImplementationOnce(
      async () =>
        new Promise(resolve => {
          release = () => {
            resolve(page({ offset: 3 }))
          }
        }),
    )

    const first = loadPage('t1', '/repo/a.ts', 3)

    await loadPage('t1', '/repo/a.ts', 3)
    expect(request).toHaveBeenCalledTimes(2)

    release()
    await first
  })

  it('restarts the walk when the file moved under it', async () => {
    request.mockResolvedValueOnce(page({ eof: false }))
    await loadPage('t1', '/repo/a.ts', 1)

    request
      .mockResolvedValueOnce(page({ offset: 3, version: 'v2' }))
      .mockResolvedValueOnce(page({ text: 'fresh', version: 'v2' }))

    await loadPage('t1', '/repo/a.ts', 3)

    expect(Object.keys($textTabs.get().t1?.pages ?? {})).toEqual(['1'])
    expect($textTabs.get().t1?.pages[1]?.text).toBe('fresh')
    expect($textTabs.get().t1?.version).toBe('v2')
  })
})

describe('reloadPages', () => {
  it('drops every page and reads the first one again', async () => {
    request.mockResolvedValueOnce(page({ eof: false }))
    await loadPage('t1', '/repo/a.ts', 1)

    request.mockResolvedValueOnce(page({ offset: 3 }))
    await loadPage('t1', '/repo/a.ts', 3)

    request.mockResolvedValueOnce(page({ text: 'after the edit', version: 'v2' }))
    await reloadPages('t1', '/repo/a.ts')

    expect(Object.keys($textTabs.get().t1?.pages ?? {})).toEqual(['1'])
    expect($textTabs.get().t1?.pages[1]?.text).toBe('after the edit')
  })

  it('keeps the reader where they were', async () => {
    request.mockResolvedValueOnce(page())
    await loadPage('t1', '/repo/a.ts', 1)
    setScroll('t1', 420)

    request.mockResolvedValueOnce(page({ text: 'reloaded', version: 'v2' }))
    await reloadPages('t1', '/repo/a.ts')

    // The pages really were read again — the offset survives that, rather than
    // surviving because nothing happened.
    expect($textTabs.get().t1?.pages[1]?.text).toBe('reloaded')
    expect($textTabs.get().t1?.scrollTop).toBe(420)
  })

  it('discards a page that settles from before the reload', async () => {
    let release = (): void => {}

    request.mockImplementationOnce(
      async () =>
        new Promise(resolve => {
          release = () => {
            resolve(page({ text: 'stale', version: 'v1' }))
          }
        }),
    )

    const stale = loadPage('t1', '/repo/a.ts', 1)

    request.mockResolvedValueOnce(page({ text: 'fresh', version: 'v2' }))
    await reloadPages('t1', '/repo/a.ts')

    release()
    await stale

    expect($textTabs.get().t1?.pages[1]?.text).toBe('fresh')
  })
})

describe('per-tab reading state', () => {
  it('remembers wrap, scroll and the navigation already answered', async () => {
    request.mockResolvedValue(page())
    await loadPage('t1', '/repo/a.ts', 1)

    toggleWrap('t1')
    setScroll('t1', 96)
    markAnswered('t1', 3)

    expect($textTabs.get().t1).toMatchObject({ answered: 3, scrollTop: 96, wrap: false })
  })

  it('writes nothing for a tab that has no bucket', () => {
    setScroll('gone', 10)

    expect($textTabs.get().gone).toBeUndefined()
  })
})
