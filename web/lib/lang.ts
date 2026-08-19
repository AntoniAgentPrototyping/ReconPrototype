import { cookies, headers } from "next/headers";

import type { Lang } from "./words";

export const LANG_COOKIE = "recon_lang";

/**
 * Which language to render in, resolved server-side.
 *
 * **The default is the browser's, not English.** The people this system is for work
 * in Vietnamese; the reason the app had `<html lang="en">` and zero Vietnamese words
 * is that nobody had got to it, not that anybody chose English. Reading
 * `Accept-Language` means a Vietnamese browser gets Vietnamese on first load without
 * anyone configuring anything, and an English browser — a maintainer's, say — still
 * gets English.
 *
 * **Vietnamese wins ties.** A browser sending no usable preference gets Vietnamese,
 * because a finance user seeing English is a worse failure than a maintainer seeing
 * Vietnamese: the maintainer can read the toggle in the header and the finance user
 * may not know there is one.
 *
 * The cookie beats both, and is the only thing the toggle writes.
 */
export async function currentLang(): Promise<Lang> {
  const jar = await cookies();
  const chosen = jar.get(LANG_COOKIE)?.value;
  if (chosen === "en" || chosen === "vi") return chosen;

  const accept = (await headers()).get("accept-language") ?? "";
  // Deliberately crude: the first language tag that is English wins, otherwise
  // Vietnamese. Weighted-quality parsing would be more correct and would change the
  // answer for essentially nobody.
  const first = accept.split(",")[0]?.trim().toLowerCase() ?? "";
  return first.startsWith("en") ? "en" : "vi";
}
