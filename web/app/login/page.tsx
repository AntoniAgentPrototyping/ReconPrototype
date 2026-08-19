import { LoginForm } from "./login-form";
import { currentLang } from "@/lib/lang";

export const dynamic = "force-dynamic";

export const metadata = { title: "Đăng nhập" };

/**
 * A server shell around the sign-in form, added in Phase 5 so the page can be
 * rendered in the reader's language.
 *
 * The form itself has to stay a client component — it uses `useActionState` for the
 * pending state and the error — and a client component cannot read cookies or
 * headers. So the language is resolved here and passed down, which is the same shape
 * every other localized page in this app uses.
 *
 * This page in particular must be translatable: it is the one screen a person sees
 * *before* they have an account, and telling somebody in English that their password
 * is wrong is not much help if English is why they cannot read the form.
 */
export default async function LoginPage() {
  return <LoginForm lang={await currentLang()} />;
}
