/**
 * One trigger-anchored panel above the composer, shared by the two stats pills.
 *
 * The same skin and the same dismissal rules the context meter's breakdown
 * already uses — a click outside or Escape closes it — because these are the
 * same kind of surface in the same place, and a second set of manners for a
 * panel 40px away would be noticed.
 *
 * Open state is *owned by the caller*, which is what lets a row of pills hold
 * one exclusive slot: opening either closes the other.
 *
 * The panel is centred on its trigger and then nudged back inside the viewport
 * if that put an edge outside it: a pill near the window edge must not open a
 * panel half off-screen.
 */

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import css from './StatDialog.module.css'

export interface StatDialogProps {
  children: ReactNode
  /** The pill; rendered in place, with the panel anchored above it. */
  anchor: ReactNode
  label: string
  onClose: () => void
  open: boolean
}

/** Clearance the panel keeps from the window's edges. */
const MARGIN = 12

export function StatDialog({ anchor, children, label, onClose, open }: StatDialogProps) {
  const root = useRef<HTMLSpanElement | null>(null)
  const panel = useRef<HTMLDivElement | null>(null)
  const [shift, setShift] = useState(0)

  // Measured at rest (shift 0) and corrected by exactly the overflow, so one
  // pass lands it: the next measurement would find nothing to do.
  useLayoutEffect(() => {
    if (!open) {
      setShift(0)

      return
    }

    const element = panel.current

    if (element === null) return

    const box = element.getBoundingClientRect()
    const past = box.right - (window.innerWidth - MARGIN)
    const before = MARGIN - box.left

    if (past > 0) setShift(-past)
    else if (before > 0) setShift(before)
  }, [open])

  useEffect(() => {
    if (!open) return

    const onPointerDown = (event: PointerEvent) => {
      if (root.current?.contains(event.target as Node) === true) return

      onClose()
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)

    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose, open])

  return (
    <span className={css.anchor} ref={root}>
      {anchor}
      {open && (
        <div
          aria-label={label}
          className={css.panel}
          ref={panel}
          role="dialog"
          style={shift === 0 ? undefined : { transform: `translateX(calc(-50% + ${shift}px))` }}
        >
          {children}
        </div>
      )}
    </span>
  )
}
