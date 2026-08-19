/**
 * Every word this application says to a person, in both languages it speaks.
 *
 * **Why a dictionary and not an i18n framework.** Eight pages. A framework brings a
 * build step, a message-extraction tool, ICU plural syntax and a runtime, to solve a
 * problem this file solves in one lookup. If this grows past a few hundred entries
 * that trade changes — it has not.
 *
 * **The Vietnamese is the team's own vocabulary wherever the team has one.** This
 * matters more than fluency. `VERDICT_OK` in `src/finance_template.py` is
 * `"ok có thể xuất HD"` and `VERDICT_BAD` is `"Cần check lại số có vấn đề"` — those
 * are the phrases the finance team writes in their own workbooks, and they appear
 * here unchanged. Inventing a more "correct" translation of *reconciliation* would
 * make the screen read like a different system from the file it produces. Where the
 * team has no word (there is no Vietnamese for "peak memory" in their workbooks),
 * the entry says the plain thing rather than a calque.
 *
 * **English first, deliberately.** The register (5.2 then 5.3) requires the jargon
 * be rewritten *before* translation, or the result is dense idiomatic Vietnamese
 * instead of dense idiomatic English. Every entry below is the rewritten English —
 * `exit code 3`, `Peak RSS`, `hard stop`, `SHA-256` and `openpyxl` do not appear.
 */

export type Lang = "en" | "vi";

export const LANGS: Lang[] = ["vi", "en"];

type Entry = { en: string; vi: string };

/**
 * The run verdicts, which are the four most-read words in the product.
 *
 * `hint` is the sentence that answers "so what do I do?", because the one-word badge
 * cannot. These four were `ok` / `variance` / `unverified` / `hard stop` with the
 * exit code in a tooltip — a vocabulary borrowed from a command line.
 */
export const VERDICTS: Record<string, Entry & { hint: Entry }> = {
  ok: {
    en: "matches",
    vi: "ok có thể xuất HD",
    hint: {
      en: "This run's totals agree with the figures the team supplied.",
      vi: "Số liệu của lần chạy này khớp với số của team.",
    },
  },
  variance: {
    en: "does not match",
    vi: "Cần check lại — số có vấn đề",
    hint: {
      en: "A real disagreement with the team's figures. The amounts are listed below — somebody has to decide which side is right.",
      vi: "Số liệu lệch thật so với số của team. Chênh lệch được liệt kê bên dưới — cần người xem lại xem bên nào đúng.",
    },
  },
  unverified: {
    en: "not checked",
    vi: "chưa đối chiếu",
    hint: {
      en: "It ran cleanly, but nothing was supplied to check it against. Not a failure — enter the team's totals on the window page.",
      vi: "Chạy xong không lỗi, nhưng chưa có số của team để đối chiếu. Không phải lỗi — nhập số của team ở trang kỳ.",
    },
  },
  hard_stop: {
    en: "stopped",
    vi: "đã dừng",
    hint: {
      en: "The run stopped before producing anything, because continuing would have produced a file that looked complete and was not. No invoice file was written.",
      vi: "Lần chạy đã dừng và không tạo ra file nào, vì nếu chạy tiếp sẽ ra một file trông có vẻ đầy đủ nhưng thật ra không. Không có file nào được ghi.",
    },
  },
};

/** Queue states, for a window whose run has not concluded yet. */
export const JOB_STATES: Record<string, Entry> = {
  queued: { en: "waiting", vi: "đang chờ" },
  leased: { en: "running", vi: "đang chạy" },
  done: { en: "finished", vi: "đã xong" },
  error: { en: "the service failed", vi: "hệ thống gặp lỗi" },
  cancelled: { en: "cancelled", vi: "đã huỷ" },
};

/**
 * Everything else, keyed by a short name.
 *
 * The keys are English-ish for readability in JSX; they are not shown to anyone.
 */
export const WORDS: Record<string, Entry> = {
  // -- the board ----------------------------------------------------------
  board: { en: "Settlement windows", vi: "Các kỳ đối soát" },
  window: { en: "Period", vi: "Kỳ" },
  run: { en: "Run", vi: "Lần chạy" },
  verdict: { en: "Result", vi: "Kết quả" },
  findings: { en: "Things to look at", vi: "Cần xem lại" },
  duration: { en: "Took", vi: "Thời gian" },
  // Was "Peak RSS" — the largest amount of memory the run needed, which is an
  // engine-port trigger measurement and means nothing to the finance team.
  memory: { en: "Memory used", vi: "Bộ nhớ đã dùng" },
  rules: { en: "Rules used", vi: "Quy tắc áp dụng" },
  rulesFrozen: { en: "frozen", vi: "đã khoá" },
  rulesFrozenHint: {
    en: "Frozen to the rules an earlier run of this period used, so a later edit cannot change what a re-run produces.",
    vi: "Đã khoá theo bộ quy tắc mà lần chạy trước của kỳ này dùng, nên chỉnh sửa sau đó không làm đổi kết quả khi chạy lại.",
  },
  rulesCurrent: { en: "current", vi: "hiện hành" },
  requestedBy: { en: "Started by", vi: "Người chạy" },
  partial: { en: "part of the stores", vi: "chỉ một phần cửa hàng" },
  partialHint: {
    en: "Only some of the expected stores are in this period, and somebody has said so on purpose. These totals are not the whole month.",
    vi: "Kỳ này chỉ có một phần số cửa hàng dự kiến, và đã có người xác nhận điều đó. Tổng số ở đây không phải cả tháng.",
  },
  storesAbsent: { en: "store(s) with no file", vi: "cửa hàng chưa có file" },
  nothingQueued: { en: "Nothing has been run yet.", vi: "Chưa có lần chạy nào." },

  // -- a run --------------------------------------------------------------
  filesProduced: { en: "Files this run produced", vi: "File lần chạy này tạo ra" },
  size: { en: "Size", vi: "Dung lượng" },
  // Was "SHA-256". It is a transfer-integrity check, and calling it by its
  // algorithm invited people to read it as a content-equality check, which it is
  // not — Excel stamps a timestamp into every file it writes.
  fingerprint: { en: "Fingerprint", vi: "Mã kiểm tra file" },
  fingerprintHint: {
    en: "Checks the file downloaded intact. It is not a check that two runs produced the same numbers — Excel writes a timestamp into every file, so two identical results still have different fingerprints.",
    vi: "Dùng để kiểm tra file tải về có nguyên vẹn không. Đây không phải cách so sánh hai lần chạy có ra cùng số hay không — Excel ghi thời điểm vào mỗi file, nên hai kết quả giống hệt nhau vẫn có mã khác nhau.",
  },
  nothingProduced: { en: "Nothing was produced.", vi: "Không có file nào được tạo." },
  diagnostics: {
    en: "Also produced, for diagnosing speed rather than for finance:",
    vi: "Ngoài ra còn có, dùng để xem tốc độ chứ không phải cho kế toán:",
  },
  runLog: { en: "What the run did, step by step", vi: "Diễn biến của lần chạy" },
  exceptions: { en: "Rows needing a decision", vi: "Dòng cần quyết định" },
  updatingLive: { en: "updating automatically", vi: "đang tự cập nhật" },

  // Timings. "Compute" was hinted as "DataFrame math" and "Serialize" as
  // "openpyxl workbook building" — both are the names of Python libraries.
  timeTotal: { en: "Total", vi: "Tổng" },
  timeReading: { en: "Reading the files", vi: "Đọc file" },
  timeCalculating: { en: "Calculating", vi: "Tính toán" },
  timeWriting: { en: "Building the Excel file", vi: "Tạo file Excel" },

  // -- a period -----------------------------------------------------------
  uploadedAs: { en: "Uploaded as", vi: "Tên file khi tải lên" },
  readAs: { en: "The system will read it as", vi: "Hệ thống sẽ đọc thành" },
  store: { en: "Store", vi: "Cửa hàng" },
  uploadedBy: { en: "Uploaded by", vi: "Người tải lên" },
  remove: { en: "Remove", vi: "Xoá" },
  confirm: { en: "Confirm", vi: "Xác nhận" },
  cancel: { en: "Cancel", vi: "Huỷ" },
  teamFigures: { en: "The team's figures", vi: "Số của team" },
  saveFigures: { en: "Save figures", vi: "Lưu số" },
  withdraw: { en: "Withdraw", vi: "Rút lại" },

  // -- rules --------------------------------------------------------------
  rulesPage: { en: "Rules", vi: "Quy tắc" },
  // Was "goldens-affecting" / "invalidates goldens". A golden is a committed
  // reference workbook; the fact a user needs is simply that this setting changes
  // the numbers.
  changesNumbers: { en: "changes the numbers", vi: "làm đổi số" },
  changesNumbersHint: {
    en: "Editing this changes what the Excel file contains. The change is checked against a known-good period before anyone relies on it.",
    vi: "Sửa mục này sẽ làm đổi nội dung file Excel. Thay đổi sẽ được đối chiếu với một kỳ đã biết chắc kết quả trước khi dùng.",
  },
  // Was the raw sha256 of the config.
  rulesVersion: { en: "Version", vi: "Phiên bản" },

  // -- everywhere ---------------------------------------------------------
  backToBoard: { en: "Back to the list", vi: "Về danh sách" },
  tryAgain: { en: "Try again", vi: "Thử lại" },
  loading: { en: "Loading…", vi: "Đang tải…" },
  language: { en: "Tiếng Việt", vi: "English" },
};

/** One lookup. Missing keys return the key, which is visible and therefore fixable. */
export function t(lang: Lang, key: keyof typeof WORDS): string {
  return WORDS[key]?.[lang] ?? String(key);
}

export function verdict(lang: Lang, status: string | null): Entry & { hint: Entry } {
  return (
    VERDICTS[status ?? ""] ?? {
      en: status ?? "unknown",
      vi: status ?? "không rõ",
      hint: { en: "", vi: "" },
    }
  );
}

export function jobState(lang: Lang, state: string): string {
  return JOB_STATES[state]?.[lang] ?? state;
}
