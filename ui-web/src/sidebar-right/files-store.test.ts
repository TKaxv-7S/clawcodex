import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import type { WorkspaceEntry } from '../gateway/protocol.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $workspace } from '../state/store.ts'
import {
  $filesTree,
  childPath,
  loadLevel,
  orderEntries,
  reloadTree,
  resetTree,
  startTree,
  toggleLevel,
} from './files-store.ts'

const request = vi.fn()

function level(entries: WorkspaceEntry[], truncated = false) {
  return { absolute_path: '/repo', entries, ok: true, truncated }
}

beforeEach(() => {
  request.mockReset()
  setGatewayClient({ request } as unknown as GatewayClient)
  $workspace.set('/repo')
  resetTree()
})

afterEach(() => {
  setGatewayClient(null)
  $workspace.set('')
  resetTree()
})

describe('orderEntries', () => {
  it('puts directories first, then everything else by name', () => {
    const ordered = orderEntries([
      { name: 'readme.md', type: 'file' },
      { name: 'link', type: 'other' },
      { name: 'src', type: 'directory' },
      { name: 'Assets', type: 'directory' },
    ])

    expect(ordered.map(entry => entry.name)).toEqual(['Assets', 'src', 'link', 'readme.md'])
  })

  it('sorts numerically, so file2 precedes file10', () => {
    const ordered = orderEntries([
      { name: 'file10.ts', type: 'file' },
      { name: 'file2.ts', type: 'file' },
    ])

    expect(ordered.map(entry => entry.name)).toEqual(['file2.ts', 'file10.ts'])
  })

  it('leaves the backend listing alone', () => {
    const entries: WorkspaceEntry[] = [
      { name: 'b', type: 'file' },
      { name: 'a', type: 'file' },
    ]

    orderEntries(entries)

    expect(entries.map(entry => entry.name)).toEqual(['b', 'a'])
  })
})

describe('childPath', () => {
  it('joins with the separator the parent already uses', () => {
    expect(childPath('/repo/src', 'app.ts')).toBe('/repo/src/app.ts')
    expect(childPath('C:\\repo\\src', 'app.ts')).toBe('C:\\repo\\src\\app.ts')
  })

  it('does not double a trailing separator', () => {
    expect(childPath('/', 'repo')).toBe('/repo')
  })
})

describe('the tree', () => {
  it('lists the root once and marks it open', async () => {
    request.mockResolvedValue(level([{ name: 'src', type: 'directory' }]))

    await startTree('/repo')
    await startTree('/repo')

    expect(request).toHaveBeenCalledTimes(1)
    expect(request).toHaveBeenCalledWith('fs.list_dir', { cwd: '/repo', path: '/repo' })
    expect($filesTree.get().expanded).toEqual(['/repo'])
  })

  it('fetches a level the first time it opens and never again', async () => {
    request.mockResolvedValue(level([]))
    await startTree('/repo')

    await toggleLevel('/repo/src')
    await toggleLevel('/repo/src')
    await toggleLevel('/repo/src')

    // Root, then the level once — collapsing keeps it, so reopening is free.
    expect(request).toHaveBeenCalledTimes(2)
    expect($filesTree.get().expanded).toContain('/repo/src')
  })

  it('keeps a level that failed rather than retrying it on reopen', async () => {
    request.mockResolvedValueOnce(level([]))
    await startTree('/repo')

    request.mockResolvedValueOnce({
      error: { code: 'workspace-file/not-found', message: 'gone' },
      ok: false,
    })
    await toggleLevel('/repo/src')

    expect($filesTree.get().levels['/repo/src']).toEqual({
      failure: { code: 'workspace-file/not-found', message: 'gone' },
      kind: 'failed',
    })

    await toggleLevel('/repo/src')
    await toggleLevel('/repo/src')

    expect(request).toHaveBeenCalledTimes(2)
  })

  it('carries truncation through, because a cut level is not a short one', async () => {
    request.mockResolvedValue(level([{ name: 'a', type: 'file' }], true))

    await startTree('/repo')

    expect($filesTree.get().levels['/repo']).toMatchObject({ kind: 'ready', truncated: true })
  })

  it('reloads exactly the levels that are open', async () => {
    request.mockResolvedValue(level([]))
    await startTree('/repo')
    await toggleLevel('/repo/src')
    await toggleLevel('/repo/docs')
    // Collapsed again: dropped rather than refetched.
    await toggleLevel('/repo/docs')

    request.mockClear()
    await reloadTree()

    expect(request.mock.calls.map(call => call[1].path).sort()).toEqual(['/repo', '/repo/src'])
    expect($filesTree.get().levels['/repo/docs']).toBeUndefined()
  })

  it('drops a listing that settles after the tree was re-rooted', async () => {
    let release = (): void => {}

    request.mockImplementationOnce(
      async () =>
        new Promise(resolve => {
          release = () => {
            resolve(level([{ name: 'stale', type: 'file' }]))
          }
        }),
    )

    const stale = loadLevel('/repo')

    request.mockResolvedValue(level([{ name: 'fresh', type: 'file' }]))
    await startTree('/other')

    release()
    await stale

    expect($filesTree.get().root).toBe('/other')
    expect($filesTree.get().levels['/repo']).toBeUndefined()
  })
})
