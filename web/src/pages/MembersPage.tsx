import { useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  useCreateInvite,
  useInvites,
  useMembers,
  useRemoveMember,
  useRevokeInvite,
  useSetAccessMode,
  useSetMemberRole,
} from "../api/hooks";
import { LoadingView, QueryError } from "../components/StateViews";
import type { AccessMode, AppRole, Member } from "../api/types";

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** One person: who they are, what they may do, and (for the owner) controls. */
function MemberRow({
  member,
  canManage,
  assignable,
  onRole,
  onRemove,
  busy,
}: {
  member: Member;
  canManage: boolean;
  assignable: { name: string; label: string }[];
  onRole: (role: AppRole | null) => void;
  onRemove: () => void;
  busy: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  // Ownership is not a role you can edit away, and nobody removes themselves.
  const editable = canManage && !member.is_owner && !member.is_self;

  return (
    <li className="member">
      <div className="member__head">
        <span className="member__name">
          {member.display_name ?? `Пользователь ${member.user_id}`}
          {member.is_self && " (это ты)"}
        </span>
        <span className="badge">{member.role_label}</span>
      </div>

      {editable && (
        <>
          <label className="field">
            <span className="field__label">Что может делать</span>
            <select
              className="homework-form__input"
              value={member.app_role ?? ""}
              disabled={busy}
              onChange={(e) =>
                onRole((e.target.value || null) as AppRole | null)
              }
            >
              <option value="">Только смотрит (роль не выдана)</option>
              {assignable.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>

          {confirming ? (
            <div className="homework__confirm">
              <span>Убрать доступ к классу?</span>
              <div className="homework__actions">
                <button
                  type="button"
                  className="row-action row-action--danger"
                  disabled={busy}
                  onClick={() => {
                    setConfirming(false);
                    onRemove();
                  }}
                >
                  Да, убрать
                </button>
                <button
                  type="button"
                  className="row-action"
                  onClick={() => setConfirming(false)}
                >
                  Отмена
                </button>
              </div>
            </div>
          ) : (
            <div className="homework__actions">
              <button
                type="button"
                className="row-action row-action--danger"
                onClick={() => setConfirming(true)}
              >
                Убрать из класса
              </button>
            </div>
          )}
        </>
      )}
    </li>
  );
}

/** Invitations. A link is shown once, right after it is created — the server
 * keeps only its hash, so it genuinely cannot be shown again. */
function InvitesSection({
  chatId,
  assignable,
}: {
  chatId: number;
  assignable: { name: string; label: string }[];
}) {
  const { data, isPending, isError, error, refetch } = useInvites(chatId, true);
  const create = useCreateInvite(chatId);
  const revoke = useRevokeInvite(chatId);
  const [role, setRole] = useState<AppRole>("viewer");
  const [freshLink, setFreshLink] = useState<string | null>(null);

  return (
    <section className="settings-section">
      <h2 className="section__title">🔗 Пригласить</h2>
      <p className="field__hint">
        Отправь ссылку человеку — он откроет приложение и получит выбранную роль.
        Ссылка одноразовая и действует сутки.
      </p>

      <label className="field">
        <span className="field__label">Кем пригласить</span>
        <select
          className="homework-form__input"
          value={role}
          onChange={(e) => setRole(e.target.value as AppRole)}
        >
          {assignable.map((r) => (
            <option key={r.name} value={r.name}>
              {r.label}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="button"
        disabled={create.isPending}
        onClick={() =>
          create.mutate(role, {
            onSuccess: (invite) => setFreshLink(invite.url ?? null),
          })
        }
      >
        {create.isPending ? "Создаём…" : "Создать ссылку"}
      </button>

      {create.isError && (
        <p className="notice">
          {errorText(create.error, "Не получилось создать ссылку.")}
        </p>
      )}

      {freshLink && (
        <div className="invite-link">
          <p className="field__label">
            Скопируй ссылку — второй раз её показать не получится:
          </p>
          <code className="invite-link__value">{freshLink}</code>
        </div>
      )}

      {isPending && <LoadingView label="Загружаем приглашения…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}
      {!isPending && !isError && data.length === 0 && (
        <p className="muted">Активных приглашений нет.</p>
      )}
      {!isPending && !isError && data.length > 0 && (
        <ul className="card-list">
          {data.map((invite) => (
            <li key={invite.id} className="audit-row">
              <span>{invite.role_label}</span>
              <span className="audit-row__time">действует до {invite.expires_at}</span>
              <div className="homework__actions">
                <button
                  type="button"
                  className="row-action row-action--danger"
                  disabled={revoke.isPending}
                  onClick={() => revoke.mutate(invite.id)}
                >
                  Отозвать
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Who can see this class and what each of them may do. Visible to every
 * member; the controls appear only for whoever the server says may manage
 * members (``can_manage``), and every change is re-checked there. */
export function MembersPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const { data, isPending, isError, error, refetch } = useMembers(id);
  const setRole = useSetMemberRole(id);
  const setMode = useSetAccessMode(id);
  const removeMember = useRemoveMember(id);

  if (isPending) return <LoadingView label="Загружаем участников…" />;
  if (isError) return <QueryError error={error} onRetry={() => refetch()} />;

  const busy = setRole.isPending || removeMember.isPending;
  const currentMode = data.access_mode_options.find(
    (o) => o.name === data.access_mode,
  );

  return (
    <main className="page">
      <h1 className="page__title">Участники</h1>

      <section className="settings-section">
        <h2 className="section__title">🔐 Кто вносит данные</h2>
        <select
          className="homework-form__input"
          value={data.access_mode}
          disabled={!data.can_manage || setMode.isPending}
          onChange={(e) => setMode.mutate(e.target.value as AccessMode)}
        >
          {data.access_mode_options.map((o) => (
            <option key={o.name} value={o.name}>
              {o.label}
            </option>
          ))}
        </select>
        <p className="field__hint">{currentMode?.description}</p>
        {data.access_mode === "telegram" && (
          <p className="field__hint">
            Пока выбран этот вариант, роли ниже ни на что не влияют — права берутся
            из того, кто админ в Telegram.
          </p>
        )}
        {setMode.isError && (
          <p className="notice">
            {errorText(setMode.error, "Не получилось переключить режим.")}
          </p>
        )}
      </section>

      {(setRole.isError || removeMember.isError) && (
        <p className="notice">
          {errorText(
            setRole.error ?? removeMember.error,
            "Не получилось изменить участника.",
          )}
        </p>
      )}

      <ul className="card-list">
        {data.members.map((member) => (
          <MemberRow
            key={member.user_id}
            member={member}
            canManage={data.can_manage}
            assignable={data.assignable_roles}
            busy={busy}
            onRole={(appRole) => setRole.mutate({ userId: member.user_id, appRole })}
            onRemove={() => removeMember.mutate(member.user_id)}
          />
        ))}
      </ul>

      {!data.can_manage && (
        <p className="muted">Менять участников может только владелец класса.</p>
      )}

      {data.can_manage && (
        <InvitesSection chatId={id} assignable={data.assignable_roles} />
      )}
    </main>
  );
}
