/**
 * A run id that does not exist. Reached from `RunPage` when the API answers 404
 * (**B2**) — previously that surfaced as an unhandled `ApiError` and the generic
 * error page, which reads as "the system is broken" rather than "that run is not
 * there".
 */
export default function RunNotFound() {
  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      <strong>No such run.</strong>
      <p className="small">
        Run numbers are assigned in order and are never reused, so this one either
        has not happened yet or the link is mistyped.
      </p>
      <p>
        <a className="button" href="/">
          Back to the board
        </a>
      </p>
    </div>
  );
}
