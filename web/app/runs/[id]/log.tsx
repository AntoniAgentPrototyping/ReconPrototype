"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { LogLine, LogPage } from "@/lib/api";

/**
 * The live run log, by polling.
 *
 * **Polling, not server-sent events, and that is the design.** The API's `seq`
 * is producer-assigned and gapless, so `?after_seq=N` serves polling now and SSE
 * later with no schema change. Streaming dies silently through corporate proxies
 * and is miserable to debug at month end (docs/06-DECISIONS.md#d32).
 *
 * `complete` from the API is what stops the poll — not an empty page, which
 * only means "nothing new yet".
 */
export function RunLog({ runId, complete }: { runId: number; complete: boolean }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [done, setDone] = useState(complete);
  const [failed, setFailed] = useState<string | null>(null);
  const cursor = useRef(-1);
  const box = useRef<HTMLPreElement>(null);
  const stuckToBottom = useRef(true);

  const poll = useCallback(async () => {
    const response = await fetch(`/runs/${runId}/log-feed?after_seq=${cursor.current}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`log unavailable (${response.status})`);
    const page = (await response.json()) as LogPage;
    if (page.lines.length > 0) {
      cursor.current = page.next_seq;
      setLines((previous) => [...previous, ...page.lines]);
    }
    return page.complete;
  }, [runId]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const finished = await poll();
        if (cancelled) return;
        if (finished) {
          setDone(true);
          return;
        }
      } catch (error) {
        if (cancelled) return;
        setFailed(error instanceof Error ? error.message : String(error));
        // Keep polling anyway: a transient blip must not require a page reload
        // in the middle of a 171-second run.
      }
      timer = setTimeout(tick, 1500);
    };

    void tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [poll]);

  // Follow the tail only while the reader is already at the bottom — yanking the
  // viewport away from someone reading an earlier warning is worse than not
  // following at all.
  useEffect(() => {
    const element = box.current;
    if (element && stuckToBottom.current) element.scrollTop = element.scrollHeight;
  }, [lines]);

  const onScroll = () => {
    const element = box.current;
    if (!element) return;
    stuckToBottom.current =
      element.scrollHeight - element.scrollTop - element.clientHeight < 40;
  };

  return (
    <div className="panel">
      <p className="small muted" style={{ marginTop: 0 }}>
        {lines.length} line{lines.length === 1 ? "" : "s"}
        {done ? " · complete" : " · streaming, polling every 1.5s"}
        {failed && <span className="badge hard_stop"> {failed}</span>}
      </p>
      <pre className="log" ref={box} onScroll={onScroll}>
        {lines.map((line) => (
          <div key={line.seq} className={line.kind}>
            {line.text || " "}
          </div>
        ))}
        {lines.length === 0 && <span className="muted">waiting for output…</span>}
      </pre>
    </div>
  );
}
