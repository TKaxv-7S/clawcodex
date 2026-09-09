/**
 * Has the file under the reader changed since the preview read it?
 *
 * The reference client answers this from a filesystem watcher on its host. This
 * gateway has no such stream, but it does carry the one event that matters
 * here: the agent finishing a write. So the answer is derived from the
 * transcript — a completed `write_file` or `edit_file` for this path, after the
 * page landed — which covers exactly the case the notice is for.
 *
 * The limit is stated rather than hidden: an edit made *outside* the agent is
 * not announced, and the Reload button is there for it.
 */

import { toolFilePath } from '../conversation/tool-view.ts'
import type { TranscriptNode } from '../state/transcript.ts'

const WRITE_TOOLS = new Set(['edit_file', 'write_file'])

export function changedSince(
  nodes: readonly TranscriptNode[],
  path: string,
  readAt: number,
  workspace?: string,
): boolean {
  if (path === '' || readAt === 0) return false

  return nodes.some(node => {
    if (node.kind !== 'tool' || !WRITE_TOOLS.has(node.name) || node.state !== 'done') return false
    // A rehydrated node carries no end time; treating that as "just now" would
    // announce a change on every resumed session.
    if (node.endedAt === undefined || node.endedAt <= readAt) return false

    return toolFilePath(node, workspace) === path
  })
}
