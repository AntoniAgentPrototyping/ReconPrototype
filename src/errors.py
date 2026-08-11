class ReconHardStop(Exception):
    """Unrecoverable data problem — the run must not produce a finance file.

    Used for: missing input folders, unmappable required columns, store-count
    mismatch. Everything softer goes to exceptions.xlsx instead.
    """
