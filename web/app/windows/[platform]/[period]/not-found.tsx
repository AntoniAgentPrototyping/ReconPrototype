/**
 * A window address the API refuses — an unknown platform, or a period that fails
 * the `_safe_period` check (**B2**).
 *
 * A window with no uploads is NOT this: that is a real, empty window and it has
 * its own page, because the first thing anyone does with a window is upload into
 * it.
 */
export default function WindowNotFound() {
  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      <strong>That is not a window address.</strong>
      <p className="small">
        Windows are named <span className="mono">platform</span> and{" "}
        <span className="mono">period</span> — for example{" "}
        <span className="mono">tiktok / 2026-05_w1</span>,{" "}
        <span className="mono">shopee / 2026-05_s1</span>, or{" "}
        <span className="mono">lazada / 2026-05_l1</span>.
      </p>
      <p>
        <a className="button" href="/">
          Back to the board
        </a>
      </p>
    </div>
  );
}
