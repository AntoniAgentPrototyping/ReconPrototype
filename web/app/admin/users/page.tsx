import { redirect } from "next/navigation";

import { api, whoami, type User } from "@/lib/api";

import CreateUserForm from "./create-user-form";
import UserActions from "./user-actions";

export const dynamic = "force-dynamic";

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "never";
}

/**
 * Accounts.
 *
 * The role check here is a courtesy — every route this page calls is admin-gated
 * in `service/api.py`, and a non-admin who forges a request gets a 403 from there.
 * Redirecting is just better than rendering a page of 403s.
 */
export default async function UsersPage() {
  const me = await whoami();
  if (!me) redirect("/login");
  if (me.must_change_password) redirect("/account/password");
  if (me.role !== "recon.admin") redirect("/");

  const { users } = await api<{ users: User[] }>("/users");
  const admins = users.filter((u) => u.role === "recon.admin" && !u.disabled_at).length;

  return (
    <>
      <h1>Accounts</h1>
      <p className="lede">
        Three roles. <span className="mono">recon.viewer</span> reads;{" "}
        <span className="mono">recon.user</span> uploads exports, queues runs and requests
        config changes; <span className="mono">recon.admin</span> also approves those requests
        and manages these accounts.
      </p>

      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>Username</th>
              <th>Role</th>
              <th>Status</th>
              <th>Created</th>
              <th>Last sign-in</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>
                  <span className="mono">{u.username}</span>
                  {u.display_name && <div className="muted small">{u.display_name}</div>}
                </td>
                <td>
                  <span className="mono">{u.role}</span>
                </td>
                <td>
                  {u.disabled_at ? (
                    <span className="badge variance">disabled</span>
                  ) : (
                    <span className="badge ok">active</span>
                  )}
                  {u.must_change_password && (
                    <div className="muted small">must change password</div>
                  )}
                </td>
                <td className="muted small">{when(u.created_at)}</td>
                <td className="muted small">{when(u.last_login_at)}</td>
                <td>
                  <UserActions
                    userId={u.id}
                    username={u.username}
                    role={u.role}
                    disabled={u.disabled_at !== null}
                    isSelf={u.username === me.subject}
                    isLastAdmin={u.role === "recon.admin" && !u.disabled_at && admins <= 1}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Add someone</h2>
      <CreateUserForm />

      <p className="muted small" style={{ maxWidth: 720 }}>
        The initial password is generated here and shown once. An admin cannot choose it — every
        audit column in this system (<span className="mono">requested_by</span>,{" "}
        <span className="mono">proposed_by</span>, <span className="mono">decided_by</span>) is
        only evidence if impersonating a colleague is hard, and an admin who picks your password
        can be you. The first thing a new user does is make it unknown again.
      </p>
      <p className="muted small" style={{ maxWidth: 720 }}>
        Accounts are disabled, never deleted, so an old run still names somebody a human
        recognises. Disabling signs them out immediately.
      </p>
    </>
  );
}
