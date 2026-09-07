import { useStore } from '@nanostores/react'
import { useEffect, useMemo, useState } from 'react'

import type { LiveAgent } from '../gateway/protocol.ts'
import {
  fetchDelegationStatus,
  interruptSubagent,
  setDelegationPaused,
} from '../state/actions.ts'
import { $delegation, $sessionId } from '../state/store.ts'
import css from './AgentsPanel.module.css'

/** How often the live view re-reads the backend snapshot. */
const POLL_MS = 2_000

/** Agents whose depth is unknown sort as top-level rather than vanishing. */
const depthOf = (agent: LiveAgent): number => agent.depth ?? 0

/** `started_at` is unix *seconds* from the Python clock, not milliseconds. */
function elapsed(agent: LiveAgent, now: number): string {
  const started = agent.started_at

  if (typeof started !== 'number' || started <= 0) return '—'

  const seconds = Math.max(0, Math.round(now / 1000 - started))

  if (seconds < 60) return `${seconds}s`

  const minutes = Math.floor(seconds / 60)

  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`
}

/**
 * Live subagents for this session, with the controls to stop them.
 *
 * The rows come from `delegation.status` — the backend supervisor both spawn
 * paths register with — rather than from accumulated progress events, so a
 * foreground delegation appears here (it never enters the task registry the
 * event stream is built from) and the list survives a reconnect.
 */
export function AgentsPanel() {
  const delegation = useStore($delegation)
  const sessionId = useStore($sessionId)
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (sessionId === null) return

    void fetchDelegationStatus()
    const timer = setInterval(() => {
      setNow(Date.now())
      void fetchDelegationStatus()
    }, POLL_MS)

    return () => {
      clearInterval(timer)
    }
  }, [sessionId])

  const agents = useMemo(() => {
    const active = delegation?.active ?? []

    // Shallowest first, then oldest first, so a parent reads above the children
    // it spawned instead of the list reordering as agents finish.
    return [...active].sort(
      (a, b) => depthOf(a) - depthOf(b) || (a.started_at ?? 0) - (b.started_at ?? 0),
    )
  }, [delegation])

  const paused = delegation?.paused === true
  const cap = delegation?.max_concurrent_children

  if (delegation === null) {
    return <div className={css.empty}>Loading agents…</div>
  }

  return (
    <div className={css.root}>
      <div className={css.header}>
        <span className={css.count}>
          {agents.length} running
          {/* A missing cap is unknown, not zero — saying "of 0" would read as
              a session that can never delegate. */}
          {typeof cap === 'number' ? ` of ${cap}` : ''}
        </span>
        <button
          aria-pressed={paused}
          className={[css.pauseButton, paused ? css.pauseButtonOn : ''].filter(Boolean).join(' ')}
          onClick={() => {
            void setDelegationPaused(!paused)
          }}
          title={
            paused
              ? 'Allow this session to spawn new agents again'
              : 'Stop this session spawning new agents; running ones continue'
          }
          type="button"
        >
          {paused ? 'Spawning paused' : 'Pause spawning'}
        </button>
      </div>

      {agents.length === 0 ? (
        <div className={css.empty}>
          {paused
            ? 'No agents running. New ones are paused.'
            : 'No agents running.'}
        </div>
      ) : (
        <ul className={css.list}>
          {agents.map(agent => {
            const id = agent.subagent_id ?? ''
            const stopping = agent.status === 'interrupted'

            return (
              <li className={css.row} key={id} style={{ paddingLeft: 12 + depthOf(agent) * 16 }}>
                <div className={css.main}>
                  <span className={css.goal} title={agent.goal}>
                    {agent.goal || 'subagent'}
                  </span>
                  <span className={css.meta}>
                    {agent.model ?? 'default model'} · {agent.tool_count ?? 0} tools ·{' '}
                    {elapsed(agent, now)}
                  </span>
                </div>
                <span className={[css.status, stopping ? css.statusStopping : ''].filter(Boolean).join(' ')}>
                  {agent.status ?? 'running'}
                </span>
                <button
                  className={css.stopButton}
                  // An interrupt already fired; the slot is held until the
                  // worker actually exits, so a second click would do nothing.
                  disabled={stopping || id === ''}
                  onClick={() => {
                    void interruptSubagent(id)
                  }}
                  title={stopping ? 'Already stopping' : 'Interrupt this agent'}
                  type="button"
                >
                  Stop
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
