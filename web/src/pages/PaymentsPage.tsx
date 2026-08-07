import { useState } from "react";
import { useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  useCreatePayment,
  useDeletePayment,
  usePayments,
  useSetPaymentPaid,
  useUpdatePayment,
} from "../api/hooks";
import { EmptyView, LoadingView, QueryError } from "../components/StateViews";
import { formatDate } from "../utils/date";
import type { Payment, PaymentPeriod } from "../api/types";

const PERIODS: { value: PaymentPeriod; label: string }[] = [
  { value: "one_time", label: "разово" },
  { value: "monthly", label: "каждый месяц" },
  { value: "per_lesson", label: "за занятие" },
];

const STATUS_LABEL: Record<string, string> = {
  paid: "Оплачено",
  overdue: "Просрочено",
  due_soon: "Скоро платить",
  upcoming: "Позже",
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** Money is typed in whole units and sent in minor ones. The conversion lives
 * here and nowhere else, and the *displayed* amount always comes back from the
 * server (``amount_text``) so the two can never disagree. */
function toMinor(input: string): number | null {
  const normalised = input.replace(",", ".").trim();
  if (!normalised) return null;
  const value = Number(normalised);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

function PaymentForm({
  initial,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: {
  initial?: Payment;
  submitLabel: string;
  pending: boolean;
  error: unknown;
  onSubmit: (values: {
    title: string;
    amount_minor: number;
    due_date: string;
    currency: string;
    period: PaymentPeriod;
    remind_days_before: number;
    note?: string;
  }) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [amount, setAmount] = useState(
    initial ? String(initial.amount_minor / 100) : "",
  );
  const [currency, setCurrency] = useState(initial?.currency ?? "UAH");
  const [dueDate, setDueDate] = useState(initial?.due_date ?? "");
  const [period, setPeriod] = useState<PaymentPeriod>(initial?.period ?? "monthly");
  const [remind, setRemind] = useState(String(initial?.remind_days_before ?? 1));
  const [note, setNote] = useState(initial?.note ?? "");
  const [amountError, setAmountError] = useState<string | null>(null);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const minor = toMinor(amount);
    if (minor === null) {
      setAmountError("Впиши сумму числом, например 350");
      return;
    }
    setAmountError(null);
    onSubmit({
      title,
      amount_minor: minor,
      due_date: dueDate,
      currency,
      period,
      remind_days_before: Number(remind) || 0,
      note: note.trim() || undefined,
    });
  }

  return (
    <form className="homework-form" onSubmit={submit}>
      <label className="field">
        <span className="field__label">За что платить</span>
        <input
          className="homework-form__input"
          placeholder="Например, занятия за март"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />
      </label>
      <div className="homework-form__row">
        <label className="field">
          <span className="field__label">Сколько</span>
          <input
            className="homework-form__input"
            inputMode="decimal"
            placeholder="350"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </label>
        <label className="field">
          <span className="field__label">Валюта</span>
          <input
            className="homework-form__input"
            value={currency}
            maxLength={16}
            onChange={(e) => setCurrency(e.target.value)}
          />
        </label>
      </div>
      {amountError && <p className="notice">{amountError}</p>}
      <label className="field">
        <span className="field__label">Когда платить</span>
        <input
          className="homework-form__input"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
          required
        />
      </label>
      <label className="field">
        <span className="field__label">Как часто</span>
        <select
          className="homework-form__input"
          value={period}
          onChange={(e) => setPeriod(e.target.value as PaymentPeriod)}
        >
          {PERIODS.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field__label">Напомнить за сколько дней</span>
        <input
          className="homework-form__input"
          type="number"
          min={0}
          max={30}
          value={remind}
          onChange={(e) => setRemind(e.target.value)}
        />
      </label>
      <label className="field">
        <span className="field__label">Примечание (не обязательно)</span>
        <input
          className="homework-form__input"
          value={note}
          maxLength={300}
          onChange={(e) => setNote(e.target.value)}
        />
      </label>
      {Boolean(error) && (
        <p className="notice">{errorText(error, "Не получилось сохранить.")}</p>
      )}
      <div className="homework-form__row">
        <button type="submit" className="button" disabled={pending}>
          {pending ? "Сохраняем…" : submitLabel}
        </button>
        <button type="button" className="button button--secondary" onClick={onCancel}>
          Отмена
        </button>
      </div>
    </form>
  );
}

function PaymentRow({
  payment,
  onEdit,
  onTogglePaid,
  onDelete,
  busy,
}: {
  payment: Payment;
  onEdit: () => void;
  onTogglePaid: () => void;
  onDelete: () => void;
  busy: boolean;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <li className={`homework homework--${payment.status}`}>
      <div className="homework__head">
        <span className="homework__subject">{payment.title}</span>
        <span className={`badge badge--${payment.status}`}>
          {STATUS_LABEL[payment.status] ?? payment.status}
        </span>
      </div>
      <p className="homework__description">
        {payment.amount_text} · {payment.period_label}
      </p>
      {payment.note && <p className="card__meta">{payment.note}</p>}
      <div className="homework__foot">
        <span className="homework__due">Платить: {formatDate(payment.due_date)}</span>
        {payment.can_edit && (
          <button
            type="button"
            className="homework__toggle"
            disabled={busy}
            onClick={onTogglePaid}
          >
            {payment.is_paid ? "Вернуть в долги" : "Оплачено"}
          </button>
        )}
      </div>

      {payment.can_edit && !confirming && (
        <div className="homework__actions">
          <button type="button" className="row-action" onClick={onEdit}>
            ✏️ Изменить
          </button>
          <button
            type="button"
            className="row-action row-action--danger"
            onClick={() => setConfirming(true)}
          >
            🗑 Удалить
          </button>
        </div>
      )}
      {confirming && (
        <div className="homework__confirm">
          <span>Удалить запись об оплате?</span>
          <div className="homework__actions">
            <button
              type="button"
              className="row-action row-action--danger"
              disabled={busy}
              onClick={() => {
                setConfirming(false);
                onDelete();
              }}
            >
              Да, удалить
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
      )}
    </li>
  );
}

/** What has to be paid and when — the tutor profile's money screen.
 *
 * Everybody in the chat can see it (being asked to pay and not being told the
 * amount would be absurd); only the owner and editors can change anything, and
 * the server decides that per entry via ``can_edit``. */
export function PaymentsPage() {
  const { chatId } = useParams();
  const id = Number(chatId);
  const { data, isPending, isError, error, refetch } = usePayments(id);
  const create = useCreatePayment(id);
  const update = useUpdatePayment(id);
  const setPaid = useSetPaymentPaid(id);
  const remove = useDeletePayment(id);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Payment | null>(null);

  function closeForms() {
    setShowForm(false);
    setEditing(null);
    create.reset();
    update.reset();
  }

  return (
    <main className="page">
      <div className="page__head">
        <h1 className="page__title">Оплата</h1>
        <button
          type="button"
          className="button"
          onClick={() => {
            setEditing(null);
            setShowForm((v) => !v);
          }}
        >
          {showForm ? "Отмена" : "＋ Добавить"}
        </button>
      </div>

      {showForm && (
        <PaymentForm
          submitLabel="Добавить"
          pending={create.isPending}
          error={create.isError ? create.error : null}
          onCancel={closeForms}
          onSubmit={(values) => create.mutate(values, { onSuccess: closeForms })}
        />
      )}

      {editing && (
        <PaymentForm
          key={editing.id}
          initial={editing}
          submitLabel="Сохранить"
          pending={update.isPending}
          error={update.isError ? update.error : null}
          onCancel={closeForms}
          onSubmit={(values) =>
            update.mutate(
              { paymentId: editing.id, input: values },
              { onSuccess: closeForms },
            )
          }
        />
      )}

      {(setPaid.isError || remove.isError) && (
        <p className="notice">
          {errorText(setPaid.error ?? remove.error, "Не получилось изменить запись.")}
        </p>
      )}

      {isPending && <LoadingView label="Загружаем оплату…" />}
      {isError && <QueryError error={error} onRetry={() => refetch()} />}
      {!isPending && !isError && data.length === 0 && (
        <EmptyView message="Записей об оплате пока нет. Нажми «＋ Добавить», чтобы записать первую." />
      )}
      {!isPending && !isError && data.length > 0 && (
        <ul className="homework-list">
          {data.map((payment) => (
            <PaymentRow
              key={payment.id}
              payment={payment}
              busy={setPaid.isPending || remove.isPending}
              onEdit={() => {
                setShowForm(false);
                update.reset();
                setEditing(payment);
              }}
              onTogglePaid={() =>
                setPaid.mutate({ paymentId: payment.id, isPaid: !payment.is_paid })
              }
              onDelete={() => remove.mutate(payment.id)}
            />
          ))}
        </ul>
      )}
    </main>
  );
}
