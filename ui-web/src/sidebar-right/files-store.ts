/**
 * The file tree's state: which directories are open, and what each one holds.
 *
 * A tree is not one address with one value — it is a listing per level, fetched
 * the first time the reader opens it — so it is view state this module owns
 * rather than anything the transcript or the gateway keeps. Levels are keyed by
 * absolute path; a collapsed level is *kept*, so reopening it draws from memory
 * instead of asking again, and a failed level is kept too: reload is the retry.
 */

import { map } from 'nanostores'

import type { WorkspaceEntry, WorkspaceFileFailure } from '../gateway/protocol.ts'
import { listWorkspaceDir } from '../state/actions.ts'

export type FilesLevel =
  | { entries: WorkspaceEntry[]; kind: 'ready'; truncated: boolean }
  | { failure: WorkspaceFileFailure; kind: 'failed' }
  | { kind: 'loading' }

export interface FilesTreeState {
  /** Absolute paths currently open, the root included. */
  expanded: string[]
  levels: Record<string, FilesLevel>
  root: string
}

export const $filesTree = map<FilesTreeState>({ expanded: [], levels: {}, root: '' })

/** Natural, case-insensitive name order, so `file2` precedes `file10`. */
const byName = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/**
 * One level in the reader's order: directories first, then everything else,
 * each group by name. The backend's order is a listing fact, not this one.
 */
export function orderEntries(entries: readonly WorkspaceEntry[]): WorkspaceEntry[] {
  return [...entries].sort((left, right) => {
    const group = Number(right.type === 'directory') - Number(left.type === 'directory')

    return group === 0 ? byName.compare(left.name, right.name) : group
  })
}

/** A child's absolute path. Separators are the tree's, never the client's guess. */
export function childPath(parent: string, name: string): string {
  const separator = parent.includes('\\') && !parent.includes('/') ? '\\' : '/'

  return parent.endsWith(separator) ? `${parent}${name}` : `${parent}${separator}${name}`
}

function setLevel(path: string, level: FilesLevel): void {
  const state = $filesTree.get()

  $filesTree.set({ ...state, levels: { ...state.levels, [path]: level } })
}

/** List one level, marking it in flight first so the row can say so. */
export async function loadLevel(path: string): Promise<void> {
  setLevel(path, { kind: 'loading' })

  const root = $filesTree.get().root
  const result = await listWorkspaceDir(path)

  // The tree was re-rooted (another session) while this listing was in flight:
  // writing it now would file a directory under a root it does not belong to.
  if ($filesTree.get().root !== root) return

  setLevel(
    path,
    result.ok
      ? { entries: result.entries, kind: 'ready', truncated: result.truncated }
      : { failure: result.error, kind: 'failed' },
  )
}

/** Point the tree at a workspace and list its root, once per root. */
export async function startTree(root: string): Promise<void> {
  if ($filesTree.get().root === root) return

  $filesTree.set({ expanded: [root], levels: {}, root })
  await loadLevel(root)
}

/** Open or close a directory; the first opening is what fetches it. */
export async function toggleLevel(path: string): Promise<void> {
  const state = $filesTree.get()
  const open = state.expanded.includes(path)

  $filesTree.set({
    ...state,
    expanded: open ? state.expanded.filter(entry => entry !== path) : [...state.expanded, path],
  })

  if (!open && state.levels[path] === undefined) await loadLevel(path)
}

/**
 * Drop every listing and ask again for the open ones.
 *
 * A level that was listed and then collapsed is dropped rather than refetched:
 * it costs a request the reader did not ask for, and opening it again will.
 */
export async function reloadTree(): Promise<void> {
  const state = $filesTree.get()

  $filesTree.set({ ...state, levels: {} })
  await Promise.all(state.expanded.map(path => loadLevel(path)))
}

export function resetTree(): void {
  $filesTree.set({ expanded: [], levels: {}, root: '' })
}
