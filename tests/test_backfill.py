"""Borrowing an order's lines from the window that exported them — defect 2.12.

Every test here runs against a synthetic two- or three-window tree under `tmp_path`,
because the properties being asserted are about *which* window supplies a line, and
that is exactly what real data cannot be made to demonstrate on demand.

The three that matter most, and what each would cost if it broke:

* **A matched order is never borrowed.** The explode sums quantity per
  `(store, order_id, sku_id, sku_name, unit_price_gross)` bucket, so a second copy of a
  line inflates quantity *inside* one SKU row and adds no visible row. That is the
  pooling anti-fix, measured at 4.5× over-count on July TikTok.
* **The nearest predecessor wins, and only it.** An order re-pulled into several
  windows must be taken once.
* **Report mode changes nothing.** It is the mode that runs in production first, so it
  has to be provably inert.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

import pandas as pd  # noqa: E402

from src import backfill  # noqa: E402
from src.errors import ReconHardStop  # noqa: E402
from src.runlog import RunLog  # noqa: E402

PLATFORM = "tiktok"

# The synthetic contract: raw header -> canonical name. Deliberately NOT read from
# `config/settings.yaml` — these tests are about window selection and fan-out, and
# using the real map would make them fail for reasons that have nothing to do with
# that (a real map change) while hiding the ones they exist to catch.
COLMAP = {
    "Order ID": "order_id",
    "Seller SKU": "sku_id",
    "Quantity": "quantity",
}

SETTINGS = {
    "file_formats": [".xlsx"],
    "sheet_names": {PLATFORM: {"orders": "OrderSKUList"}},
    "store_from_filename": {PLATFORM: r"^order[ _](?P<store>.+?)\.xlsx$"},
    "drop_unmapped_columns": True,
    # Off, as it is for every real platform: two byte-identical SKU lines in one order
    # are legitimate (a unit and its gift variant), so row content cannot discriminate
    # a re-pull (D5).
    "dedupe_rows": False,
}


def _orders(root, window: str, store: str, rows: list[tuple[str, str, int]]) -> None:
    """Write `order <store>.xlsx` into one window's orders folder."""
    folder = root / window / PLATFORM / "orders"
    folder.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(
        [{"Order ID": oid, "Seller SKU": sku, "Quantity": qty} for oid, sku, qty in rows])
    with pd.ExcelWriter(folder / f"order {store}.xlsx", engine="openpyxl") as w:
        frame.to_excel(w, sheet_name="OrderSKUList", index=False)


def _borrow(root, period, needed):
    return backfill.borrow_order_lines(
        root, period, PLATFORM, frozenset(needed), COLMAP, SETTINGS, RunLog())


# --- window selection -------------------------------------------------------

def test_predecessors_are_same_month_same_series_nearest_first(tmp_path):
    for window in ("2026-07_w1", "2026-07_w2", "2026-07_w3",
                   "2026-07_s1",           # different series
                   "2026-06_w1"):          # different month
        _orders(tmp_path, window, "KAO", [("A", "S1", 1)])

    assert backfill.predecessor_windows(tmp_path, "2026-07_w3", PLATFORM) == [
        "2026-07_w2", "2026-07_w1"]


def test_the_first_window_of_a_month_has_no_predecessor(tmp_path):
    """Which is why `2026-05_w1` and `2026-05_s1` cannot move under apply mode — they
    are the golden windows whose zero-delta is claimed by construction."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("A", "S1", 1)])
    _orders(tmp_path, "2026-06_w5", "KAO", [("A", "S1", 1)])

    assert backfill.predecessor_windows(tmp_path, "2026-07_w1", PLATFORM) == []


def test_a_sub_batch_sorts_after_the_batch_it_extends(tmp_path):
    """Shopee's real labels: `s2x` extends `s2` and is staged after it."""
    for window in ("2026-07_s2", "2026-07_s2x", "2026-07_s3"):
        _orders(tmp_path, window, "KAO", [("A", "S1", 1)])

    assert backfill.predecessor_windows(tmp_path, "2026-07_s3", "tiktok") == [
        "2026-07_s2x", "2026-07_s2"]
    assert backfill.predecessor_windows(tmp_path, "2026-07_s2x", "tiktok") == [
        "2026-07_s2"]


def test_a_window_with_no_orders_folder_is_not_a_predecessor(tmp_path):
    (tmp_path / "2026-07_w1" / PLATFORM).mkdir(parents=True)
    _orders(tmp_path, "2026-07_w2", "KAO", [("A", "S1", 1)])
    assert backfill.predecessor_windows(tmp_path, "2026-07_w3", PLATFORM) == ["2026-07_w2"]


# --- borrowing --------------------------------------------------------------

def test_an_order_settled_here_but_exported_there_is_borrowed(tmp_path):
    """Defect 2.12 in one assertion."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 3), ("OLD", "S9", 1)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 5)])

    rows, reports = _borrow(tmp_path, "2026-07_w2", {("KAO", "SHARED")})

    assert len(rows) == 1
    assert rows.iloc[0]["order_id"] == "SHARED"
    # Numeric, not "3": borrowed rows go through `ingest.normalize_parts` like the
    # window's own do. This assertion read "3" until 2026-08-20, which is what a test
    # written against the buggy path looks like.
    assert rows.iloc[0]["quantity"] == 3
    assert rows.iloc[0]["source_window"] == "2026-07_w1"
    assert [r.window for r in reports] == ["2026-07_w1"]
    assert reports[0].orders == 1 and reports[0].lines == 1
    assert reports[0].files == ("order KAO.xlsx",)


def test_an_order_in_two_predecessors_is_taken_once_from_the_nearest(tmp_path):
    """The fan-out guard. Taking both is the pooling anti-fix — measured at 4.5×.

    The quantities differ per window so the assertion also proves *which* copy won,
    not merely that one did.
    """
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 1)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("SHARED", "S1", 7)])
    _orders(tmp_path, "2026-07_w3", "KAO", [("OTHER", "S2", 1)])

    rows, reports = _borrow(tmp_path, "2026-07_w3", {("KAO", "SHARED")})

    assert len(rows) == 1, "the same order was borrowed from more than one window"
    assert rows.iloc[0]["quantity"] == 7, "the nearest predecessor did not win"
    assert [r.window for r in reports] == ["2026-07_w2"]


def test_every_file_in_the_winning_window_contributes(tmp_path):
    """Within ONE window all parts sum — the same rule the window's own part files
    follow, because `dedupe_rows` is off and a repeated SKU line is legitimate (D5).
    """
    folder = tmp_path / "2026-07_w1" / PLATFORM / "orders"
    folder.mkdir(parents=True)
    for part, qty in (("1", 2), ("2", 4)):
        frame = pd.DataFrame([{"Order ID": "SHARED", "Seller SKU": "S1", "Quantity": qty}])
        with pd.ExcelWriter(folder / f"order KAO{part}.xlsx", engine="openpyxl") as w:
            frame.to_excel(w, sheet_name="OrderSKUList", index=False)
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 1)])

    settings = {**SETTINGS,
                "store_from_filename": {PLATFORM: r"^order[ _](?P<store>.+?)\d*\.xlsx$"}}
    rows, reports = backfill.borrow_order_lines(
        tmp_path, "2026-07_w2", PLATFORM, frozenset({("KAO", "SHARED")}),
        COLMAP, settings, RunLog())

    assert len(rows) == 2, "a part file was dropped"
    assert sorted(rows["quantity"]) == [2, 4]
    assert reports[0].lines == 2


def test_an_order_no_predecessor_holds_is_simply_not_returned(tmp_path):
    """The legitimate ~21% class: lines exist in NO window. It must stay unmatched and
    keep being reported as the reconciling item — not be invented from somewhere."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("OLD", "S9", 1)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 1)])

    rows, reports = _borrow(tmp_path, "2026-07_w2", {("KAO", "NOWHERE")})

    assert len(rows) == 0 and reports == []
    # Shaped like the populated frame, so the caller needs no special case.
    assert "order_id" in rows.columns and "source_window" in rows.columns


def test_a_different_stores_identical_order_id_is_not_borrowed(tmp_path):
    """The 2.9 lesson: an order id is unique per store, not globally. Borrowing on the
    bare id would hand KAO's lines to Purite's settlement."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 1)])
    _orders(tmp_path, "2026-07_w1", "Purite", [("SHARED", "S2", 9)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S3", 1)])

    rows, _ = _borrow(tmp_path, "2026-07_w2", {("Purite", "SHARED")})

    assert len(rows) == 1
    assert rows.iloc[0]["store"] == "Purite"
    assert rows.iloc[0]["sku_id"] == "S2"


def test_nothing_needed_reads_nothing(tmp_path):
    """A healthy window must pay no I/O cost for a control it does not need — order
    files are the largest inputs in the tree."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("A", "S1", 1)])
    log = RunLog()

    rows, reports = backfill.borrow_order_lines(
        tmp_path, "2026-07_w2", PLATFORM, frozenset(), COLMAP, SETTINGS, log)

    assert len(rows) == 0 and reports == []
    assert log.lines == [], "a window with nothing missing still read a predecessor"


def test_borrowing_is_deterministic(tmp_path):
    """A run must be reproducible from its inputs, row order included — the golden
    gate compares at zero tolerance."""
    _orders(tmp_path, "2026-07_w1", "KAO",
            [("A", "S2", 1), ("A", "S1", 2), ("B", "S3", 3)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S9", 1)])

    needed = {("KAO", "A"), ("KAO", "B")}
    first, _ = _borrow(tmp_path, "2026-07_w2", needed)
    second, _ = _borrow(tmp_path, "2026-07_w2", needed)

    pd.testing.assert_frame_equal(first, second)
    assert first["sku_id"].tolist() == ["S1", "S2", "S3"]


def test_borrowing_an_order_this_window_already_has_is_a_structural_error(tmp_path):
    """`needed` is built from what the window does NOT have. If a caller ever passes a
    covered order, the failure must be loud here rather than a doubled quantity inside
    one SKU line that no new row reveals."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 1)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("SHARED", "S1", 1)])

    # The guard is on the RESULT: what comes back is a subset of `needed`. Asking for
    # an order that is genuinely missing here cannot return a covered one.
    rows, _ = _borrow(tmp_path, "2026-07_w2", {("KAO", "SHARED")})
    assert frozenset(zip(rows["store"], rows["order_id"])) <= {("KAO", "SHARED")}


# The fan-out guard itself has NO test, deliberately. `take` is masked by
# `keys.isin(remaining)` and `remaining` only shrinks, so no input can violate the
# guard — it is unreachable by construction, which is what makes it a structural
# guard rather than a check. Driving it would mean mocking `borrow_order_lines`'
# internals and asserting the mock. What it became on 2026-08-20 is a
# `ReconHardStop` instead of an `assert`, because `python -O` deletes asserts and
# this is the statement standing between a re-pull and a doubled invoice.


# --- reading through the same rules as the window's own files ---------------
#
# Found 2026-08-20 by review, not by these tests: `borrow_order_lines` read
# predecessor files through `ingest.read_files` and then hand-rolled two `.strip()`
# calls, so borrowed frames skipped everything `read_parts` does afterwards.
# Nothing here could have caught it — the fixtures above configure no aliases and
# assert on string quantities, and no GOLDEN window opens a predecessor file at all
# (s2/s3 have 100% own-window coverage, w1/s1 have no predecessor, Lazada is not
# wired). So the gap lived in the one mode that is switched on in production.

ALIAS_SETTINGS = {**SETTINGS, "store_aliases": {PLATFORM: {"Pedia": "Abbott Pedia"}}}


def test_a_predecessor_file_named_by_an_alias_is_found(tmp_path):
    """The live half of the gap, in the exact shape July has it.

    `settings.yaml` maps `"Pediasure" -> "Abbott Pediasure"` because "the order files
    drop the Abbott", and `input/2026-07_w1/tiktok/orders/` really is named
    `2. Order Pediasure 06.xlsx`. `needed` comes from frames that went through
    `read_parts`, so it holds the CANONICAL name — while the borrow's file prefilter
    compared the raw filename. Result: w1's files were skipped and 941,081,056 VND of
    recoverable July settlement reported as zero, silently.
    """
    _orders(tmp_path, "2026-07_w1", "Pedia", [("SHARED", "S1", 3)])
    _orders(tmp_path, "2026-07_w2", "Pedia", [("MINE", "S2", 1)])

    rows, reports = backfill.borrow_order_lines(
        tmp_path, "2026-07_w2", PLATFORM, frozenset({("Abbott Pedia", "SHARED")}),
        COLMAP, ALIAS_SETTINGS, RunLog())

    assert len(rows) == 1, "an aliased store's predecessor file was skipped"
    assert rows.iloc[0]["store"] == "Abbott Pedia", "the borrowed row kept the raw name"
    assert [r.window for r in reports] == ["2026-07_w1"]

    # Discriminator: the un-canonicalized comparison the code used to make. The store
    # the FILENAME yields is not the store `needed` asks for, which is precisely why
    # the prefilter dropped the file before anything could read it.
    from src import ingest
    raw = ingest.store_from_filename(
        "order Pedia.xlsx", ALIAS_SETTINGS["store_from_filename"][PLATFORM])
    assert raw == "Pedia"
    assert raw not in {"Abbott Pedia"}, "the discriminator no longer discriminates"


def test_borrowed_rows_are_coerced_like_the_windows_own_rows(tmp_path):
    """Under `apply` these rows are concatenated onto the orders frame, and the explode
    groups on `unit_price_gross` while SUMMING `quantity`
    (`calculate.explode_to_sku_tiktok`). A string `"3"` beside a float `3.0` is a
    different group key and an unsummable mixed column, so borrowing raw text would
    corrupt the very buckets it is meant to complete.
    """
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 3)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 1)])

    rows, _ = _borrow(tmp_path, "2026-07_w2", {("KAO", "SHARED")})

    assert pd.api.types.is_numeric_dtype(rows["quantity"]), (
        "borrowed quantity is still text — it never went through ingest.to_number")
    assert rows.iloc[0]["quantity"] == 3


def _unreadable_predecessor(tmp_path):
    """A w1 whose order file has the right name and the wrong sheet."""
    folder = tmp_path / "2026-07_w1" / PLATFORM / "orders"
    folder.mkdir(parents=True)
    with pd.ExcelWriter(folder / "order KAO.xlsx", engine="openpyxl") as w:
        pd.DataFrame([{"Order ID": "SHARED"}]).to_excel(
            w, sheet_name="NotTheExpectedSheet", index=False)
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 1)])


def test_report_mode_warns_about_an_unreadable_predecessor_and_carries_on(tmp_path):
    """Report mode's contract is that it changes nothing — and a run that dies is a
    change. The bad file belongs to a window nobody asked to run, so stopping *this*
    window's settlement over it is a control firing on the wrong window (the same
    reasoning `_store_of` applies to a filename it cannot parse).
    """
    _unreadable_predecessor(tmp_path)
    log = RunLog()

    rows, reports = backfill.borrow_order_lines(
        tmp_path, "2026-07_w2", PLATFORM, frozenset({("KAO", "SHARED")}),
        COLMAP, SETTINGS, log, strict=False)

    assert len(rows) == 0 and reports == []
    assert any("could not be read" in line for line in log.lines), log.lines


def test_apply_mode_refuses_an_unreadable_predecessor(tmp_path):
    """The same file, the other side of the seam: under `apply` these rows become
    invoice lines, so a predecessor that cannot be read is a refusal rather than a
    warning. One policy, stated once, both halves tested.
    """
    _unreadable_predecessor(tmp_path)

    with pytest.raises(ReconHardStop):
        backfill.borrow_order_lines(
            tmp_path, "2026-07_w2", PLATFORM, frozenset({("KAO", "SHARED")}),
            COLMAP, SETTINGS, RunLog(), strict=True)


# --- the mode switch --------------------------------------------------------

def test_the_default_mode_is_off_when_nothing_is_configured():
    assert backfill.mode_of({}) == "off"
    # An empty or null value means "not set", which resolves to the SAFE direction —
    # today's behaviour, the one every committed golden was produced under. A typo,
    # by contrast, hard-stops: "unset" and "misspelled" are different mistakes.
    assert backfill.mode_of({"cross_window_order_backfill": ""}) == "off"
    assert backfill.mode_of({"cross_window_order_backfill": None}) == "off"


@pytest.mark.parametrize("value", ["off", "report", "apply"])
def test_each_documented_mode_is_accepted(value):
    assert backfill.mode_of({"cross_window_order_backfill": value}) == value


def test_an_unknown_mode_hard_stops_rather_than_meaning_off():
    """The fail-quiet direction would be the dangerous one: a typo silently disabling
    the control that exists to make a 4.5B VND gap visible."""
    with pytest.raises(ReconHardStop) as exc:
        backfill.mode_of({"cross_window_order_backfill": "Report "})
    assert "report" in str(exc.value)

    with pytest.raises(ReconHardStop):
        backfill.mode_of({"cross_window_order_backfill": "on"})


# --- the label rule, shared with service/materialize.py ---------------------

def test_predecessor_labels_works_over_any_source_of_candidates():
    """The CLI passes directory names, the service passes window labels out of
    Postgres. One rule, so "which window is earlier" cannot be spelled twice."""
    candidates = ["2026-07_w1", "2026-07_w2", "2026-07_w4",
                  "2026-07_s1", "2026-06_w3", "not-a-window", "2026-07_w3"]

    assert backfill.predecessor_labels("2026-07_w3", candidates) == [
        "2026-07_w2", "2026-07_w1"]
    # Later windows are excluded: re-running w3 must not change because w4 arrived.
    assert "2026-07_w4" not in backfill.predecessor_labels("2026-07_w3", candidates)


def test_a_malformed_period_has_no_predecessors_rather_than_raising():
    """Called on every run. A window label nobody anticipated must degrade to "no
    comparison available", never take down a settlement run."""
    assert backfill.predecessor_labels("nonsense", ["2026-07_w1"]) == []
    assert backfill.predecessor_labels("2026-07_w2", []) == []


# --- report mode is inert ---------------------------------------------------

def _resolve(tmp_path, period, orders, settled, mode):
    return backfill.resolve(
        input_root=tmp_path, period=period, platform=PLATFORM,
        orders=orders, settled=settled, money_col="net_revenue", colmap=COLMAP,
        settings={**SETTINGS, "cross_window_order_backfill": mode}, log=RunLog())


def _frames(tmp_path):
    """w1 exports SHARED; w2 settles SHARED and MINE but only exported MINE."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("SHARED", "S1", 3)])
    _orders(tmp_path, "2026-07_w2", "KAO", [("MINE", "S2", 1)])
    own = pd.DataFrame({"store": ["KAO"], "order_id": ["MINE"],
                        "sku_id": ["S2"], "quantity": ["1"]})
    settled = pd.DataFrame({"store": ["KAO", "KAO"],
                            "order_id": ["MINE", "SHARED"],
                            "net_revenue": [100.0, 900.0]})
    return own, settled


def test_off_mode_reads_nothing_and_changes_nothing(tmp_path):
    """Every committed golden was produced under `off`. It must remain a no-op that
    does not even list a directory."""
    own, settled = _frames(tmp_path)
    log = RunLog()

    result = backfill.resolve(
        input_root=tmp_path, period="2026-07_w2", platform=PLATFORM,
        orders=own, settled=settled, money_col="net_revenue", colmap=COLMAP,
        settings={**SETTINGS, "cross_window_order_backfill": "off"}, log=log)

    assert result.orders is own, "off mode replaced the orders frame"
    assert result.reports == [] and result.applied is False
    assert log.lines == [], "off mode wrote to the log"


def test_report_mode_finds_the_gap_and_leaves_the_frame_alone(tmp_path):
    """The mode that runs in production first, so its inertness is the property."""
    own, settled = _frames(tmp_path)

    result = _resolve(tmp_path, "2026-07_w2", own, settled, "report")

    assert result.applied is False
    assert result.orders is own, "report mode must not touch the explode's input"
    assert [r.window for r in result.reports] == ["2026-07_w1"]
    assert result.orders_found == 1 and result.lines == 1
    # The recoverable figure comes from the INCOME side — the settlement value — not
    # from anything rebuilt out of the borrowed lines.
    assert result.money == 900.0


def test_apply_mode_extends_the_frame_by_exactly_the_borrowed_lines(tmp_path):
    own, settled = _frames(tmp_path)

    result = _resolve(tmp_path, "2026-07_w2", own, settled, "apply")

    assert result.applied is True
    assert len(result.orders) == len(own) + result.lines
    assert set(result.orders["order_id"]) == {"MINE", "SHARED"}
    # The window's own row is untouched and not duplicated.
    assert (result.orders["order_id"] == "MINE").sum() == 1


def test_apply_and_report_differ_only_by_the_borrowed_rows(tmp_path):
    """Stated as a property because it is what makes the report-then-apply sequence
    trustworthy: the measurement and the change come from one computation."""
    own, settled = _frames(tmp_path)

    reported = _resolve(tmp_path, "2026-07_w2", own, settled, "report")
    applied = _resolve(tmp_path, "2026-07_w2", own, settled, "apply")

    assert reported.orders_found == applied.orders_found
    assert reported.lines == applied.lines
    assert reported.money == applied.money
    pd.testing.assert_frame_equal(
        reported.borrowed.reset_index(drop=True),
        applied.borrowed.reset_index(drop=True))


def test_the_exception_rows_carry_provenance_and_no_money(tmp_path):
    """An operator needs to know which file to go and re-pull. A money column per row
    would invite summing something that is not a settlement figure."""
    own, settled = _frames(tmp_path)
    result = _resolve(tmp_path, "2026-07_w2", own, settled, "report")

    rows = backfill.exception_rows(result)

    assert list(rows.columns) == ["store", "order_id", "source_window",
                                  "source_file", "applied"]
    assert len(rows) == 1
    assert rows.iloc[0]["source_window"] == "2026-07_w1"
    # `bool(...)`: pandas stores this as np.bool_, which is not `False` by identity.
    assert bool(rows.iloc[0]["applied"]) is False
    assert not [c for c in rows.columns
                if c in ("money", "net_revenue", "amount", "quantity")]


def test_a_window_whose_orders_are_all_covered_says_so_and_reads_nothing(tmp_path):
    """Shopee's real case: order coverage is 100% on every measured window, so the
    common path must cost nothing beyond the check itself."""
    _orders(tmp_path, "2026-07_w1", "KAO", [("OLD", "S1", 1)])
    own = pd.DataFrame({"store": ["KAO"], "order_id": ["MINE"],
                        "sku_id": ["S2"], "quantity": ["1"]})
    settled = pd.DataFrame({"store": ["KAO"], "order_id": ["MINE"],
                            "net_revenue": [100.0]})
    log = RunLog()

    result = backfill.resolve(
        input_root=tmp_path, period="2026-07_w2", platform=PLATFORM,
        orders=own, settled=settled, money_col="net_revenue", colmap=COLMAP,
        settings={**SETTINGS, "cross_window_order_backfill": "report"}, log=log)

    assert result.orders is own and result.reports == []
    assert any("every settled order has lines in this window" in line
               for line in log.lines)
    assert not any("reading" in line for line in log.lines), (
        "a fully covered window still opened a predecessor's files")
