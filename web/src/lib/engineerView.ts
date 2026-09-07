/**
 * The engineer-view switch.
 *
 * Fingerprints, digests, `identity` and `environment` are the fields that make
 * a result auditable, and also the fields that make the page unreadable to
 * anyone who did not write the harness. Rather than choose, the page hides
 * them behind one persisted toggle: off, a reader sees conclusions; on, an
 * engineer sees everything the record actually contains.
 *
 * Note what the toggle does NOT hide: the raw measurement key under every
 * human label stays visible either way. That is the two-layer rule, and it is
 * not a preference.
 */

import { useCallback, useEffect, useState } from "react";

const ENGINEER_KEY = "csb.engineerView";

function read(): boolean {
  try {
    return localStorage.getItem(ENGINEER_KEY) === "1";
  } catch {
    return false; // storage unavailable (private mode) — default to the calm view
  }
}

/** Notified whenever the toggle flips, so every mounted consumer agrees. */
const listeners = new Set<(value: boolean) => void>();

export function useEngineerView(): { engineerView: boolean; toggleEngineerView: () => void } {
  const [engineerView, setEngineerView] = useState<boolean>(read);

  useEffect(() => {
    listeners.add(setEngineerView);
    return () => {
      listeners.delete(setEngineerView);
    };
  }, []);

  const toggleEngineerView = useCallback(() => {
    const next = !read();
    try {
      localStorage.setItem(ENGINEER_KEY, next ? "1" : "0");
    } catch {
      // best effort: the toggle still applies for this session
    }
    for (const listener of listeners) listener(next);
  }, []);

  return { engineerView, toggleEngineerView };
}
