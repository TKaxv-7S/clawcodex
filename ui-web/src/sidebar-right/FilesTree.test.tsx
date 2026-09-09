import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GatewayClient } from '../gateway/client.ts'
import type { WorkspaceEntry } from '../gateway/protocol.ts'
import { setGatewayClient } from '../state/actions.ts'
import { $sessionId, $workspace } from '../state/store.ts'
import { resetTree } from './files-store.ts'
import { FilesTree, workspaceTitle } from './FilesTree.tsx'
import { $activeTabId, $tabs, resetSidebar } from './store.ts'

const request = vi.fn()

function level(entries: WorkspaceEntry[], truncated = false) {
  return { absolute_path: '/repo', entries, ok: true, truncated }
}

beforeEach(() => {
  request.mockReset()
  setGatewayClient({ request } as unknown as GatewayClient)
  $sessionId.set('s1')
  $workspace.set('/repo')
  resetTree()
  resetSidebar()
})

afterEach(() => {
  cleanup()
  setGatewayClient(null)
  $sessionId.set(null)
  $workspace.set('')
  resetTree()
  resetSidebar()
})

describe('workspaceTitle', () => {
  it('is the final segment, or the root itself when it has none', () => {
    expect(workspaceTitle('/home/me/repo')).toBe('repo')
    expect(workspaceTitle('/')).toBe('/')
  })
})

describe('FilesTree', () => {
  it('says so when the session has no workspace, and asks for nothing', () => {
    $workspace.set('')

    render(<FilesTree />)

    expect(screen.getByText('This session has no workspace directory.')).toBeTruthy()
    expect(request).not.toHaveBeenCalled()
  })

  it('lists the workspace root, directories first', async () => {
    request.mockResolvedValue(
      level([
        { name: 'readme.md', type: 'file' },
        { name: 'src', type: 'directory' },
      ]),
    )

    const { container } = render(<FilesTree />)

    await waitFor(() => {
      expect(screen.getByText('src')).toBeTruthy()
    })
    expect([...container.querySelectorAll('[data-files-entry]')].map(row =>
      row.getAttribute('data-files-entry'),
    )).toEqual(['directory', 'file'])
    expect(screen.getByText('repo')).toBeTruthy()
  })

  it('lists a directory the first time it is opened', async () => {
    request.mockResolvedValueOnce(level([{ name: 'src', type: 'directory' }]))

    render(<FilesTree />)

    const folder = await screen.findByText('src')

    request.mockResolvedValueOnce(level([{ name: 'app.ts', type: 'file' }]))
    fireEvent.click(folder)

    await waitFor(() => {
      expect(screen.getByText('app.ts')).toBeTruthy()
    })
    expect(request).toHaveBeenLastCalledWith('fs.list_dir', {
      path: '/repo/src',
      session_id: 's1',
    })
  })

  it('opens a file in the column, by its absolute path', async () => {
    request.mockResolvedValue(level([{ name: 'readme.md', type: 'file' }]))

    render(<FilesTree />)

    fireEvent.click(await screen.findByText('readme.md'))

    expect($tabs.get().map(tab => tab.id)).toContain('text:/repo/readme.md')
    expect($activeTabId.get()).toBe('text:/repo/readme.md')
  })

  it('shows an entry that cannot be opened without offering a click', async () => {
    request.mockResolvedValue(level([{ name: 'socket', type: 'other' }]))

    const { container } = render(<FilesTree />)

    await waitFor(() => {
      expect(screen.getByText('socket')).toBeTruthy()
    })
    expect(container.querySelector('[data-files-entry="other"] button')).toBeNull()
  })

  it('says a level was cut rather than showing a short one', async () => {
    request.mockResolvedValue(level([{ name: 'a', type: 'file' }], true))

    render(<FilesTree />)

    expect(await screen.findByText('Too many entries; showing only some of them.')).toBeTruthy()
  })

  it('says an empty directory is empty', async () => {
    request.mockResolvedValue(level([]))

    render(<FilesTree />)

    expect(await screen.findByText('Empty directory')).toBeTruthy()
  })

  it('says why a level could not be listed, in terms of the directory', async () => {
    request.mockResolvedValue({
      error: { code: 'workspace-file/not-found', message: 'gone' },
      ok: false,
    })

    const { container } = render(<FilesTree />)

    expect(
      await screen.findByText('That directory is gone. It may have been moved or deleted.'),
    ).toBeTruthy()
    // The code is on the row too: "failed" alone cannot say failed with *what*.
    expect(container.querySelector('[data-files-code="workspace-file/not-found"]')).not.toBeNull()
  })

  it('reloads the open levels on the header control', async () => {
    request.mockResolvedValue(level([{ name: 'a', type: 'file' }]))

    render(<FilesTree />)
    await screen.findByText('a')

    request.mockClear()
    request.mockResolvedValue(level([{ name: 'b', type: 'file' }]))
    fireEvent.click(screen.getByLabelText('Reload'))

    await waitFor(() => {
      expect(screen.getByText('b')).toBeTruthy()
    })
  })
})
