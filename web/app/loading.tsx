/**
 * Shown while a server component is fetching (**B2**).
 *
 * Every page here is `force-dynamic` and several fan out to two or three API
 * calls, so a blank viewport during the wait is normal and reads as a hang. This
 * is deliberately a sentence rather than a spinner: it says what is happening.
 */
export default function Loading() {
  return (
    <div className="panel muted" aria-live="polite">
      Loading…
    </div>
  );
}
