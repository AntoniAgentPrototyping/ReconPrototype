"use client";

import { useEffect } from "react";

/**
 * What a user sees when a page throws. Until now: Next's default overlay in
 * development and a bare unstyled stack in production (**B2**).
 *
 * The rule this follows is the same one `service/api.py` follows at the API
 * boundary — a person gets a sentence they can act on, and the detail lives
 * somewhere role-gated. A traceback in a browser is not "more helpful", it is a
 * disclosure of module paths and internal structure to whoever is looking at the
 * screen, and it tells a finance user nothing.
 *
 * `digest` IS shown. It is an opaque hash Next assigns to the error, it discloses
 * nothing, and it is the one thing that lets somebody say "this one" when they
 * report it.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Server-side logging is where the detail belongs. Console here is the only
    // channel this component has, and it costs nothing.
    console.error(error);
  }, [error]);

  return (
    <div className="panel variance" style={{ maxWidth: 720 }}>
      <strong>Something went wrong on this page.</strong>
      <p className="small">
        Nothing was changed by the failure — this page only reads. Try again, and if
        it keeps happening, report it with the reference below.
      </p>
      {error.digest && (
        <p className="small">
          Reference: <span className="mono">{error.digest}</span>
        </p>
      )}
      <p>
        <button type="button" onClick={reset}>
          Try again
        </button>{" "}
        <a className="button secondary" href="/">
          Back to the board
        </a>
      </p>
    </div>
  );
}
