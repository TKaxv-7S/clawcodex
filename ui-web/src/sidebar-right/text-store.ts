/**
 * The text preview's reading state, one bucket per open file tab.
 *
 * It lives here rather than in the body so that switching tabs unmounts a
 * preview without dropping what it read: a tab comes back to the page it was
 * on, at the offset the reader left it, wrapped the way they set it.
 *
 * Two rules about versions, both enforced in `applyPage` so they are testable
 * without a socket:
 *
 * - a **first** page from a newer version replaces everything read before it —
 *   the reader is now looking at the new file, whole;
 * - a **later** page from a newer version is not merged. Two versions stitched
 *   together would read as one file that never existed, so the walk restarts
 *   from page one instead.
 */

import { map } from 'nanostores'

import type { FilePage, WorkspaceFileFailure } from '../gateway/protocol.ts'
import { readWorkspaceFile } from '../state/actions.ts'

/** One page's lines, as the backend counted them. */
export interface TextPage {
  /**
   * Zero means "past the end of the file"; one with an empty `text` means one
   * empty line. The count is the backend's, never derived from the text.
   */
  lines: number
  text: string
}

export interface TextTabState {
  /** The navigation revision this bucket has already jumped for. */
  answered: number
  eof: boolean
  failure?: WorkspaceFileFailure
  loading: boolean
  /** The path the backend resolved, which is what the header shows. */
  path: string
  /** Keyed by the 1-based line the page starts at. */
  pages: Record<number, TextPage>
  /** When the most recent page landed — the clock the change notice reads. */
  readAt: number
  scrollTop: number
  /** The file version every loaded page came from. */
  version: string
  wrap: boolean
}

export const $textTabs = map<Record<string, TextTabState>>({})

/** Wrap is on until the reader turns it off: a preview column is narrow. */
export function emptyTextTab(path: string): TextTabState {
  return {
    answered: 0,
    eof: false,
    loading: false,
    pages: {},
    path,
    readAt: 0,
    scrollTop: 0,
    version: '',
    wrap: true,
  }
}

/**
 * A read's generation, per tab. A reload bumps it, and a page that settles
 * from an older generation writes nothing — which is what keeps a slow first
 * page from landing on top of the reload that replaced it.
 */
const generations = new Map<string, number>()

/** Fold one settled page into a bucket. Pure: the whole version rule is here. */
export function applyPage(
  state: TextTabState,
  offset: number,
  page: FilePage,
): { restart: boolean; state: TextTabState } {
  const moved = state.version !== '' && state.version !== page.version

  if (moved && offset !== 1) {
    // The file changed under the walk. Drop what was read and start again from
    // the first page rather than stitch two versions into one file.
    return {
      restart: true,
      state: { ...state, eof: false, failure: undefined, loading: false, pages: {}, version: '' },
    }
  }

  // A first page replaces everything: it is either the start of the walk or a
  // reload, and in both cases what came before it is gone.
  const base = offset === 1 ? {} : state.pages

  return {
    restart: false,
    state: {
      ...state,
      eof: page.eof,
      failure: undefined,
      loading: false,
      path: page.absolute_path,
      pages: { ...base, [offset]: { lines: page.lines, text: page.text } },
      readAt: Date.now(),
      version: page.version,
    },
  }
}

/** The loaded pages in file order, each with the line it starts at. */
export function loadedPages(
  pages: Record<number, TextPage>,
): { lines: number; offset: number; text: string }[] {
  return Object.entries(pages)
    .map(([offset, page]) => ({ ...page, offset: Number(offset) }))
    .sort((left, right) => left.offset - right.offset)
}

/** A page's lines. `lines: 0` is no lines, not one empty one. */
export function linesOf(page: TextPage): string[] {
  return page.lines === 0 ? [] : page.text.split('\n')
}

/** The last line the loaded pages reach; 0 before the first page. */
export function lastLineLoaded(pages: { lines: number; offset: number }[]): number {
  const last = pages.at(-1)

  return last === undefined ? 0 : last.offset + last.lines - 1
}

function patch(tabId: string, next: Partial<TextTabState>): void {
  const current = $textTabs.get()[tabId]

  if (current === undefined) return

  $textTabs.setKey(tabId, { ...current, ...next })
}

/**
 * Read one page into a tab's bucket, seeding the bucket on the first call.
 *
 * Failures are shown, not thrown: the pages already read stay on screen with
 * one line at the end saying why the next one is not there.
 */
export async function loadPage(tabId: string, path: string, offset: number): Promise<void> {
  const existing = $textTabs.get()[tabId]

  if (existing === undefined) $textTabs.setKey(tabId, { ...emptyTextTab(path), loading: true })
  else if (existing.loading) return
  else patch(tabId, { failure: undefined, loading: true })

  const generation = (generations.get(tabId) ?? 0) + 1

  generations.set(tabId, generation)

  const result = await readWorkspaceFile(path, offset)

  if (generations.get(tabId) !== generation) return

  const current = $textTabs.get()[tabId]

  if (current === undefined) return

  if (!result.ok) {
    $textTabs.setKey(tabId, { ...current, failure: result.error, loading: false })

    return
  }

  const applied = applyPage(current, offset, result)

  $textTabs.setKey(tabId, applied.state)

  if (applied.restart) await loadPage(tabId, path, 1)
}

/**
 * Read the file again from its first page, keeping the reader where they are.
 *
 * Deliberately not "re-fetch every page that was loaded": that is several
 * sequential reads before anything can be shown, and after an edit the loaded
 * range no longer describes the same lines anyway. The scroll offset is kept,
 * which can land the reader in empty space when they were deep in a long file.
 */
export async function reloadPages(tabId: string, path: string): Promise<void> {
  const current = $textTabs.get()[tabId]

  generations.set(tabId, (generations.get(tabId) ?? 0) + 1)
  $textTabs.setKey(tabId, {
    ...(current ?? emptyTextTab(path)),
    eof: false,
    failure: undefined,
    loading: false,
    pages: {},
    version: '',
  })

  await loadPage(tabId, path, 1)
}

export function setScroll(tabId: string, scrollTop: number): void {
  patch(tabId, { scrollTop })
}

export function toggleWrap(tabId: string): void {
  const current = $textTabs.get()[tabId]

  if (current !== undefined) patch(tabId, { wrap: !current.wrap })
}

/** Record that a navigation has been jumped for, so a remount does not re-jump. */
export function markAnswered(tabId: string, revision: number): void {
  patch(tabId, { answered: revision })
}

/** Drop a closed tab's bucket; a reopened tab reads its first page again. */
export function forgetTextTab(tabId: string): void {
  const next = { ...$textTabs.get() }

  delete next[tabId]
  generations.delete(tabId)
  $textTabs.set(next)
}

export function resetTextTabs(): void {
  generations.clear()
  $textTabs.set({})
}
