import type { Metadata } from "next";

import { signOut } from "./actions";
import { LangToggle } from "./lang-toggle";
import { whoami } from "@/lib/api";
import { currentLang } from "@/lib/lang";
import { t } from "@/lib/words";
import "./globals.css";

export const metadata: Metadata = {
  // B10: every page rendered one browser-tab title. A person with four windows open
  // at month end had four identical tabs. `%s` is filled by each page's own
  // `metadata.title`; this template is what makes them distinguishable.
  title: { template: "%s · Recon", default: "Recon" },
  description: "Đối soát doanh thu sàn thương mại điện tử — settlement reconciliation",
};

/**
 * Never cache a page: everything here is a live view of a queue, and a stale
 * month board would be actively misleading at month end.
 */
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const [me, lang] = await Promise.all([whoami(), currentLang()]);

  return (
    // B7: was hardcoded `en` on a product used by a Vietnamese finance team. The
    // attribute is not decoration — it is what a screen reader picks a voice from
    // and what a browser offers to translate.
    <html lang={lang}>
      <body>
        <header className="top">
          <span className="brand">recon</span>
          {/* Suppressed entirely while a temp password is outstanding: every link
              here would 403, so offering them is just a row of dead ends. */}
          {me && !me.must_change_password && (
            <nav>
              <a href="/">{t(lang, "board")}</a>
              <a href="/config">{t(lang, "rulesPage")}</a>
              <a href="/account/password">
                {lang === "vi" ? "Tài khoản của tôi" : "My account"}
              </a>
              {me.role === "recon.admin" && (
                <a href="/admin/users">{lang === "vi" ? "Người dùng" : "People"}</a>
              )}
            </nav>
          )}
          <span className="spacer" />
          <LangToggle lang={lang} />
          {me ? (
            <>
              <span className="who">
                {me.display_name ?? me.subject} · <span className="mono">{me.role}</span>
              </span>
              <form action={signOut}>
                <button className="secondary" type="submit">
                  {lang === "vi" ? "Đăng xuất" : "Sign out"}
                </button>
              </form>
            </>
          ) : (
            <span className="who">{lang === "vi" ? "chưa đăng nhập" : "not signed in"}</span>
          )}
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
