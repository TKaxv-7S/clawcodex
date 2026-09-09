import { useState } from 'react'

import { formatMs, formatTokens, type TrajectoryStats } from '../state/trajectory.ts'
import { DatabaseIcon, GaugeIcon } from '../ui/icons.tsx'
import { qualifiedModel } from './ModelSelect.tsx'
import { StatDialog } from './StatDialog.tsx'
import dialog from './StatDialog.module.css'
import css from './StatsPills.module.css'

export interface StatsPillsProps {
  model?: string
  /** Nano mode chip (session.info.nano) — rides the model segment. */
  nano?: boolean
  provider?: string
  stats: TrajectoryStats
}

/**
 * The three disjoint prompt-side buckets the run was billed for.
 *
 * The same sum `cacheHitRatio` divides into, so the pill's percentage and the
 * dialog's counts describe one arithmetic rather than two.
 */
export function billedInput(stats: TrajectoryStats): number {
  return stats.uncachedInputTokens + stats.cacheReadTokens + stats.cacheWriteTokens
}

function exact(value: number): string {
  return `${value.toLocaleString()} tok`
}

/**
 * A cache-hit share that never rounds a partial hit up to a full one.
 *
 * `100%` has to mean every prompt token came from cache; a 99.7% run reading
 * `100% cached` is the one reading a person would act on differently. Precision
 * escalates until the figure stays under 100, and a genuine full hit is `100`.
 */
export function cacheHitPercent(ratio: number): string {
  if (ratio >= 1) return '100'

  for (const places of [0, 1, 2, 3]) {
    const shown = (ratio * 100).toFixed(places)

    if (Number(shown) < 100) return shown
  }

  return '99.999'
}

/** Output speed: the digit matters below 10, where `0 tok/s` would be a lie. */
export function formatSpeed(tps: number): string {
  return tps >= 10 ? tps.toFixed(0) : String(Math.round(tps * 10) / 10)
}

/**
 * The run's totals under the composer: what it did, and what it cost.
 *
 * Two pills rather than one line of figures. The line this replaced put turn
 * counts, two wall times, two rates and two token totals in one row: it
 * crowded as the run went on, gave time and billing figures no grouping, and
 * the exact token counts appeared nowhere at all — the compact total was the
 * only reading available. A pill per family, each opening the figures behind
 * it, gives every number a home and puts the exact counts one click away.
 *
 * The model rides the row because "156 tok/s" only means something once you
 * know what produced it, and the composer's chip shows the model without the
 * provider that served it.
 */
export function StatsPills({ model, nano = false, provider, stats }: StatsPillsProps) {
  // One exclusive slot: opening either dialog closes the other.
  const [open, setOpen] = useState<'time' | 'usage' | null>(null)
  const named = model !== undefined && model !== ''
  const billed = billedInput(stats)
  const total = billed + stats.outputTokens
  const timed =
    stats.llmMs > 0 || stats.toolMs > 0 || stats.ttftMs !== null || stats.throughput !== null

  // Before the first turn there are no figures, and a bar holding nothing but
  // separators is worse than no bar.
  if (stats.steps === 0 && total === 0 && !named) return null

  const counts = `${stats.turns} ${stats.turns === 1 ? 'turn' : 'turns'} · ${stats.steps} ${
    stats.steps === 1 ? 'step' : 'steps'
  }`
  const speed = stats.throughput === null ? null : `${formatSpeed(stats.throughput)} tok/s`
  const cacheHit =
    stats.cacheHitRatio === null ? null : `${cacheHitPercent(stats.cacheHitRatio)}% cached`
  const countsLabel = speed === null ? counts : `${counts} · ${speed}`

  const gauge = (
    <>
      <GaugeIcon size={14} />
      <span className={css.label}>
        {counts}
        {speed !== null && (
          <>
            <span aria-hidden className={css.sep}>
              ·
            </span>
            {speed}
          </>
        )}
      </span>
    </>
  )

  return (
    <div className={css.root} data-composer-stats>
      {named && (
        <span className={css.model}>
          {qualifiedModel(model ?? '', provider)}
          {/* The chip rides the model segment (like the TUI's stats line), so
              whatever narrows this row can never shed the mode without also
              shedding the model it describes. */}
          {nano && <span className={css.nano}>nano</span>}
        </span>
      )}

      {stats.steps > 0 &&
        // A run with no timed figure has no rows to show, so the pill stays a
        // plain reading rather than a button that opens an empty dialog.
        (timed ? (
          <StatDialog
            anchor={
              <button
                aria-expanded={open === 'time'}
                aria-haspopup="dialog"
                aria-label={countsLabel}
                className={css.pill}
                onClick={() => {
                  setOpen(open === 'time' ? null : 'time')
                }}
                type="button"
              >
                {gauge}
              </button>
            }
            label="Session stats"
            onClose={() => {
              setOpen(null)
            }}
            open={open === 'time'}
          >
            <div className={dialog.title}>
              <span className={dialog.titleLabel}>
                <GaugeIcon size={14} />
                Session stats
              </span>
              <span className={dialog.titleValue}>{counts}</span>
            </div>
            <div aria-hidden className={dialog.rule} />
            <dl className={dialog.rows} data-session-stats-time>
              {stats.llmMs > 0 && (
                <>
                  <dt>Model time</dt>
                  <dd>{formatMs(stats.llmMs)}</dd>
                </>
              )}
              {stats.toolMs > 0 && (
                <>
                  <dt>Tool time</dt>
                  <dd>{formatMs(stats.toolMs)}</dd>
                </>
              )}
              {stats.ttftMs !== null && (
                <>
                  <dt>TTFT avg</dt>
                  <dd>{formatMs(stats.ttftMs)}</dd>
                </>
              )}
              {stats.throughput !== null && (
                <>
                  <dt>Output speed</dt>
                  <dd>{formatSpeed(stats.throughput)} tok/s</dd>
                </>
              )}
            </dl>
          </StatDialog>
        ) : (
          <span className={css.pill}>{gauge}</span>
        ))}

      {total > 0 && (
        <StatDialog
          anchor={
            <button
              aria-expanded={open === 'usage'}
              aria-haspopup="dialog"
              aria-label={cacheHit === null ? exact(total) : `${exact(total)} · ${cacheHit}`}
              className={css.pill}
              onClick={() => {
                setOpen(open === 'usage' ? null : 'usage')
              }}
              type="button"
            >
              <DatabaseIcon size={14} />
              <span className={css.label}>
                {formatTokens(total)} tok
                {cacheHit !== null && (
                  <>
                    <span aria-hidden className={css.sep}>
                      ·
                    </span>
                    {cacheHit}
                  </>
                )}
              </span>
            </button>
          }
          label="Token usage"
          onClose={() => {
            setOpen(null)
          }}
          open={open === 'usage'}
        >
          <div className={dialog.title}>
            <span className={dialog.titleLabel}>
              <DatabaseIcon size={14} />
              Token usage
            </span>
            <span className={dialog.titleValue}>{exact(total)}</span>
          </div>
          <div aria-hidden className={dialog.rule} />
          <dl className={dialog.rows} data-session-stats-usage>
            {cacheHit !== null && (
              <>
                <dt>Cache hit</dt>
                <dd>{cacheHitPercent(stats.cacheHitRatio ?? 0)}%</dd>
              </>
            )}
            <dt>Input</dt>
            <dd>{exact(stats.uncachedInputTokens)}</dd>
            <dt>Cache read</dt>
            <dd>{exact(stats.cacheReadTokens)}</dd>
            <dt>Cache write</dt>
            <dd>{exact(stats.cacheWriteTokens)}</dd>
            <dt>Output</dt>
            <dd>{exact(stats.outputTokens)}</dd>
          </dl>
        </StatDialog>
      )}
    </div>
  )
}
