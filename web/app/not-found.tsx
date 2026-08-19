/**
 * A URL that does not exist (**B2**). Next's default is an unstyled 404 outside
 * the app's own shell, which reads like the service is broken rather than like a
 * mistyped address.
 */
export default function NotFound() {
  return (
    <div className="panel" style={{ maxWidth: 720 }}>
      <strong>There is nothing at this address.</strong>
      <p className="small">
        The link may be out of date, or the run or window it pointed at may have
        been removed.
      </p>
      <p>
        <a className="button" href="/">
          Back to the board
        </a>
      </p>
    </div>
  );
}
