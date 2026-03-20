import { useEffect } from "react"

/**
 * Escape hatch for one-time mount effects (DOM focus, third-party widgets,
 * browser subscriptions). Prefer declarative patterns over useEffect — see
 * .ai/rules.md "no-useEffect" rule for alternatives.
 */
// eslint-disable-next-line no-restricted-syntax
export function useMountEffect(effect: () => void | (() => void)) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(effect, [])
}
