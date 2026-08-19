"use client";

import { useTransition } from "react";

import { setLanguage } from "./actions";

/**
 * The language switch (**B7**).
 *
 * In the header, on every page including the login page — somebody who cannot read
 * the sign-in form needs this *before* they have an account, so it cannot live in
 * account settings.
 *
 * The label is always the language you would be switching TO, written in that
 * language. That is the one form of this control that works when you cannot read
 * the interface you are currently looking at.
 */
export function LangToggle({ lang }: { lang: "en" | "vi" }) {
  const [pending, start] = useTransition();
  const other = lang === "vi" ? "en" : "vi";

  return (
    <button
      type="button"
      className="secondary"
      disabled={pending}
      lang={other}
      onClick={() => start(async () => setLanguage(other))}
      title={other === "vi" ? "Chuyển sang tiếng Việt" : "Switch to English"}
    >
      {other === "vi" ? "Tiếng Việt" : "English"}
    </button>
  );
}
