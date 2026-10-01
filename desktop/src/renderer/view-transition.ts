import { flushSync } from 'react-dom'

// Chromium handles panel snapshots; streaming updates never call this helper.
let activeTransition: ViewTransition | undefined
export function transitionView(update: () => void) {
  if (!document.startViewTransition || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    update()
    return
  }
  activeTransition?.skipTransition()
  const transition = document.startViewTransition(() => flushSync(update))
  activeTransition = transition
  void transition.ready.catch(() => undefined) // A rapid second action may skip the snapshots.
  void transition.finished.finally(() => {
    if (activeTransition === transition) activeTransition = undefined
  }).catch(() => undefined)
}
