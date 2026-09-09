/**
 * The right column: a tab strip and whatever the active tab draws.
 *
 * One surface per window, holding the session facts, the workspace tree, and a
 * tab per file the conversation opened. The strip is the only chrome — a type's
 * own controls (wrap, reload) belong in its body, not up here beside the
 * column's.
 *
 * Fullscreen takes the whole frame rather than widening the column: a file worth
 * reading is worth reading at full width, and the conversation is still one
 * click away.
 */

import { useStore } from '@nanostores/react'

import { CollapseIcon, ExpandIcon, FolderIcon, LayersIcon, XIcon } from '../ui/icons.tsx'
import { FilesTree } from './FilesTree.tsx'
import { SessionTab } from './SessionTab.tsx'
import { TextPreview } from './TextPreview.tsx'
import {
  $activeTabId,
  $fullscreen,
  $tabs,
  closeSidebar,
  closeTab,
  focusTab,
  openPage,
  toggleFullscreen,
  type SidebarTab,
} from './store.ts'
import css from './SidebarRight.module.css'

function TabBody({ tab }: { tab: SidebarTab }) {
  if (tab.kind === 'session') return <SessionTab />
  if (tab.kind === 'files') return <FilesTree />

  return <TextPreview path={tab.address} tabId={tab.id} />
}

export function SidebarRight() {
  const tabs = useStore($tabs)
  const activeId = useStore($activeTabId)
  const fullscreen = useStore($fullscreen)

  const active = tabs.find(tab => tab.id === activeId) ?? tabs[0]

  return (
    <div className={css.root} data-fullscreen={fullscreen ? '' : undefined}>
      <div className={css.strip}>
        {/* The tablist is the chips alone: the controls after them act on the
            column, not on any one tab, and a tablist that contained them would
            make a screen reader announce four extra "tabs". */}
        <div className={css.chips} role="tablist">
          {tabs.map(tab => (
            <div
              className={[css.chip, tab.id === active?.id ? css.chipActive : '']
                .filter(Boolean)
                .join(' ')}
              key={tab.id}
            >
              <button
                aria-selected={tab.id === active?.id}
                className={css.chipTitle}
                onClick={() => {
                  focusTab(tab.id)
                }}
                role="tab"
                title={tab.address === '' ? tab.title : tab.address}
                type="button"
              >
                {tab.title}
              </button>
              {/* The session tab is the column's floor: closing the last tab
                  would leave a strip around nothing, so it stays. */}
              {tab.kind !== 'session' && (
                <button
                  aria-label={`Close ${tab.title}`}
                  className={css.chipClose}
                  onClick={() => {
                    closeTab(tab.id)
                  }}
                  type="button"
                >
                  <XIcon size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
        <span className={css.fill} />
        <button
          aria-label="Workspace files"
          className={css.tool}
          onClick={() => {
            openPage('files')
          }}
          title="Workspace files"
          type="button"
        >
          <FolderIcon size={16} />
        </button>
        <button
          aria-label="Session details"
          className={css.tool}
          onClick={() => {
            openPage('session')
          }}
          title="Session details"
          type="button"
        >
          <LayersIcon size={16} />
        </button>
        <button
          aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
          aria-pressed={fullscreen}
          className={css.tool}
          onClick={toggleFullscreen}
          title={fullscreen ? 'Leave full screen' : 'Full screen'}
          type="button"
        >
          {fullscreen ? <CollapseIcon size={16} /> : <ExpandIcon size={16} />}
        </button>
        <button
          aria-label="Close the sidebar"
          className={css.tool}
          onClick={closeSidebar}
          title="Close the sidebar (⌘I)"
          type="button"
        >
          <XIcon size={16} />
        </button>
      </div>
      <div
        aria-label={active === undefined ? undefined : active.title}
        className={css.body}
        role="tabpanel"
      >
        {active === undefined ? null : <TabBody tab={active} />}
      </div>
    </div>
  )
}
