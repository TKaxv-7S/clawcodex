/**
 * One file, read a page at a time.
 *
 * The body draws what the tab's bucket holds and asks for the next page when
 * the reader reaches the end of it. Everything it keeps — pages, scroll offset,
 * wrap, the navigation it has already answered — lives in the store, so
 * switching tabs unmounts this component without losing the reader's place.
 *
 * A changed file is *announced*, not applied: reloading under a reader loses
 * their place, and a file the agent is writing changes repeatedly. The bar
 * waits for a click.
 */

import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useRef } from 'react'

import { $transcript, $workspace } from '../state/store.ts'
import { RefreshIcon, WrapIcon } from '../ui/icons.tsx'
import { changedSince } from './changed.ts'
import { fileFailureLine } from './failure-line.ts'
import { $navigation } from './store.ts'
import {
  $textTabs,
  lastLineLoaded,
  linesOf,
  loadedPages,
  loadPage,
  markAnswered,
  reloadPages,
  setScroll,
  toggleWrap,
} from './text-store.ts'
import css from './TextPreview.module.css'

export interface TextPreviewProps {
  path: string
  tabId: string
}

/** Pages a jump-to-line may load on its own before it gives up and stops. */
const WALK_PAGE_LIMIT = 5

/**
 * Put one line at the top of the body. A line not loaded leaves it alone.
 *
 * Measured as the gap between the two boxes rather than through `offsetTop`,
 * which reports a distance from the nearest *positioned* ancestor — the app
 * frame, several boxes up — and would land the line 76-odd pixels above the
 * viewport, i.e. off the top of it.
 */
export function scrollToLine(body: HTMLElement, line: number): void {
  const row = body.querySelector(`[data-preview-line="${line}"]`)

  if (!(row instanceof HTMLElement)) return

  body.scrollTop += row.getBoundingClientRect().top - body.getBoundingClientRect().top
}

export function TextPreview({ path, tabId }: TextPreviewProps) {
  const tabs = useStore($textTabs)
  const navigation = useStore($navigation)
  const transcript = useStore($transcript)
  const workspace = useStore($workspace)
  const body = useRef<HTMLDivElement | null>(null)

  const state = tabs[tabId]
  const line = navigation[tabId]?.line
  const revision = navigation[tabId]?.revision ?? 0
  const pages = useMemo(() => loadedPages(state?.pages ?? {}), [state?.pages])
  const loadedThrough = lastLineLoaded(pages)
  const started = state !== undefined
  const hasPages = pages.length > 0

  // The first mount reads the first page; a body returning to a tab that
  // already has pages reads nothing, because the store outlived it.
  useEffect(() => {
    if (!started) void loadPage(tabId, path, 1)
  }, [started, tabId, path])

  // Come back where the reader was, once there is something to scroll. Keyed on
  // page presence alone, so recording a scroll never re-lands the body.
  useEffect(() => {
    const element = body.current

    if (hasPages && element !== null) element.scrollTop = $textTabs.get()[tabId]?.scrollTop ?? 0
  }, [hasPages, tabId])

  // Answer a navigation once. A line the pages do not reach yet loads the next
  // page — again, until they cover it or the file ends — because pages load in
  // order and there is no seek.
  //
  // Bounded, though: each page is a round trip whose backend re-reads every
  // line before it, so walking to line 200,000 would be a hundred sequential
  // reads costing quadratic work. Past the bound the reader is left at the end
  // of the loaded text with Load more, which is honest about the cost.
  useEffect(() => {
    const element = body.current

    if (state === undefined || element === null || state.answered === revision) return

    if (line === undefined) {
      markAnswered(tabId, revision)

      return
    }

    if (line > loadedThrough && !state.eof && pages.length < WALK_PAGE_LIMIT) {
      if (!state.loading && state.failure === undefined) {
        void loadPage(tabId, path, loadedThrough + 1)
      }

      return
    }

    scrollToLine(element, line)
    markAnswered(tabId, revision)
    // Recorded here as well as by the scroll event, so the bucket holds the
    // landing before anything else reads it.
    setScroll(tabId, element.scrollTop)
  }, [
    line,
    loadedThrough,
    pages.length,
    path,
    revision,
    state?.answered,
    state?.eof,
    state?.failure,
    state?.loading,
    tabId,
    started,
  ])

  const rows = useMemo(
    () =>
      pages.map(page => (
        <pre className={css.page} key={page.offset}>
          {linesOf(page).map((content, index) => {
            const number = page.offset + index

            return (
              <div
                className={[css.line, number === line ? css.lineTarget : '']
                  .filter(Boolean)
                  .join(' ')}
                data-preview-line={number}
                key={number}
              >
                {content}
                {'\n'}
              </div>
            )
          })}
        </pre>
      )),
    [pages, line],
  )

  if (state === undefined) {
    return (
      <div className={css.status} data-preview-state="loading">
        Reading…
      </div>
    )
  }

  const next = loadedThrough + 1
  const reload = () => {
    void reloadPages(tabId, path)
  }
  const changed = changedSince(transcript.nodes, path, state.readAt, workspace)

  return (
    <div className={css.root} data-preview-state="text">
      {changed && (
        <p className={css.notice} data-preview-changed>
          <span>The file has changed; this is the older text.</span>
          <button className={css.action} onClick={reload} type="button">
            Reload
          </button>
        </p>
      )}
      <div className={css.header}>
        <div className={css.path} data-preview-path title={state.path}>
          {state.path}
        </div>
        <button
          aria-label="Wrap lines"
          aria-pressed={state.wrap}
          className={[css.tool, state.wrap ? css.toolOn : ''].filter(Boolean).join(' ')}
          onClick={() => {
            toggleWrap(tabId)
          }}
          title="Wrap lines"
          type="button"
        >
          <WrapIcon size={14} />
        </button>
        <button
          aria-label="Read the file again"
          className={css.tool}
          onClick={reload}
          title="Read the file again"
          type="button"
        >
          <RefreshIcon size={14} />
        </button>
      </div>
      <div
        className={[css.body, state.wrap ? css.wrap : ''].filter(Boolean).join(' ')}
        data-preview-body
        onScroll={event => {
          setScroll(tabId, event.currentTarget.scrollTop)
        }}
        ref={body}
      >
        {rows}
        {state.failure !== undefined && (
          <p className={css.statusLine} data-preview-failed={state.failure.code}>
            <span>{fileFailureLine(state.failure)}</span>
            <button
              className={css.action}
              onClick={() => {
                void loadPage(tabId, path, next)
              }}
              type="button"
            >
              Retry
            </button>
          </p>
        )}
        {!state.eof && state.failure === undefined && (
          <button
            className={css.more}
            data-preview-more
            disabled={state.loading}
            onClick={() => {
              void loadPage(tabId, path, next)
            }}
            type="button"
          >
            {state.loading ? 'Reading…' : 'Load more'}
          </button>
        )}
      </div>
    </div>
  )
}
