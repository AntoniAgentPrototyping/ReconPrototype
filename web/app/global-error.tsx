"use client";

/**
 * The last resort: a failure in the root layout itself, where `error.tsx` cannot
 * render because there is no layout left to render it into. It has to supply its
 * own `<html>` and `<body>`.
 *
 * Deliberately plain and dependency-free — no shared CSS, no imports beyond React.
 * A page shown *because the shell failed* must not depend on the shell.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          margin: 0,
          padding: "48px 24px",
          background: "#111",
          color: "#eee",
        }}
      >
        <h1 style={{ fontSize: 20 }}>Recon is not able to load.</h1>
        <p style={{ maxWidth: 560, lineHeight: 1.5 }}>
          This is a failure in the application shell rather than in anything you did.
          No data was changed. Reload the page; if it persists, the service needs
          looking at.
        </p>
        {error.digest && (
          <p style={{ fontFamily: "ui-monospace, monospace", opacity: 0.7 }}>
            Reference: {error.digest}
          </p>
        )}
        <button
          type="button"
          onClick={reset}
          style={{ padding: "8px 14px", cursor: "pointer" }}
        >
          Reload
        </button>
      </body>
    </html>
  );
}
