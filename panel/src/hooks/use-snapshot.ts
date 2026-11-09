"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import type { Snapshot } from "@/lib/types"

export const REFRESH_MS = 5_000

export type SnapshotState = {
  snapshot: Snapshot | null
  error: string | null
  refreshing: boolean
  refresh: () => void
}

/**
 * Reads /api/snapshot now, every `REFRESH_MS` and whenever the tab becomes visible again.
 * When a refresh fails, the last snapshot stays on screen and `error` says why.
 */
export function useSnapshot(): SnapshotState {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const inFlight = useRef(false)

  const refresh = useCallback(() => {
    if (inFlight.current) {
      return
    }
    inFlight.current = true
    setRefreshing(true)
    fetch("/api/snapshot", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`the panel server answered ${response.status}`)
        }
        setSnapshot((await response.json()) as Snapshot)
        setError(null)
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => {
        inFlight.current = false
        setRefreshing(false)
      })
  }, [])

  useEffect(() => {
    const first = window.setTimeout(refresh, 0)
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        refresh()
      }
    }, REFRESH_MS)
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        refresh()
      }
    }
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(timer)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [refresh])

  return { snapshot, error, refreshing, refresh }
}
