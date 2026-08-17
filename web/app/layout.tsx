import type { Metadata } from "next";

import { signOut } from "./actions";
import { whoami } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = {
  title: "Recon",
  description: "Settlement reconciliation — runs, exceptions and rules",
};

/**
 * Never cache a page: everything here is a live view of a queue, and a stale
 * month board would be actively misleading at month end.
 */
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const me = await whoami();

  return (
    <html lang="en">
      <body>
        <header className="top">
          <span className="brand">recon</span>
          {/* Suppressed entirely while a temp password is outstanding: every link
              here would 403, so offering them is just a row of dead ends. */}
          {me && !me.must_change_password && (
            <nav>
              <a href="/">Board</a>
              <a href="/config">Config</a>
              <a href="/account/password">Account</a>
              {me.role === "recon.admin" && <a href="/admin/users">Accounts</a>}
            </nav>
          )}
          <span className="spacer" />
          {me ? (
            <>
              <span className="who">
                {me.display_name ?? me.subject} · <span className="mono">{me.role}</span>
              </span>
              <form action={signOut}>
                <button className="secondary" type="submit">
                  Sign out
                </button>
              </form>
            </>
          ) : (
            <span className="who">not signed in</span>
          )}
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
