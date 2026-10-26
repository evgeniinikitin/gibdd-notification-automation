"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { api, ApiError } from "@/lib/api";
import type { AdminTenant, User } from "@/lib/types";

type BalanceDraft = { available: string; note: string };

function apiMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Не удалось выполнить операцию";
}

function readableDate(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

export function AdminOverview() {
  const [user, setUser] = useState<User | null>(null);
  const [tenants, setTenants] = useState<AdminTenant[]>([]);
  const [drafts, setDrafts] = useState<Record<string, BalanceDraft>>({});
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const loadTenants = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const currentUser = await api.me();
      setUser(currentUser);
      if (currentUser.role !== "admin") {
        setTenants([]);
        return;
      }
      const rows = await api.adminTenants();
      setTenants(rows);
      setDrafts(
        Object.fromEntries(
          rows.map((tenant) => [
            tenant.id,
            { available: String(tenant.available), note: "" },
          ]),
        ),
      );
    } catch (caught) {
      setError(apiMessage(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadTenants(), 0);
    return () => window.clearTimeout(timer);
  }, [loadTenants]);

  const filteredTenants = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return tenants;
    return tenants.filter((tenant) =>
      [tenant.name, tenant.primaryAdminEmail, tenant.primaryAdminFullName]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(normalized)),
    );
  }, [query, tenants]);

  const totals = useMemo(
    () => ({
      active: tenants.filter((tenant) => tenant.isActive).length,
      trips: tenants.reduce((sum, tenant) => sum + tenant.tripsCount, 0),
      submitted: tenants.reduce((sum, tenant) => sum + tenant.submittedTripsCount, 0),
      available: tenants.reduce((sum, tenant) => sum + tenant.available, 0),
    }),
    [tenants],
  );

  async function saveBalance(tenant: AdminTenant) {
    const draft = drafts[tenant.id];
    const available = Number(draft?.available);
    if (!Number.isInteger(available) || available < 0) {
      setError("Количество уведомлений должно быть целым числом от 0");
      return;
    }
    setSavingId(tenant.id);
    setError(null);
    setSuccess(null);
    try {
      await api.setTenantBalance(tenant.id, available, draft?.note.trim() ?? "");
      const rows = await api.adminTenants();
      setTenants(rows);
      setDrafts((current) => ({
        ...current,
        [tenant.id]: { available: String(available), note: "" },
      }));
      setSuccess(`Баланс компании «${tenant.name}» обновлён`);
    } catch (caught) {
      setError(apiMessage(caught));
    } finally {
      setSavingId(null);
    }
  }

  if (loading) {
    return (
      <div className="page-view admin-page">
        <div className="profile-load-state"><span className="shell-spinner" />Загружаем компании…</div>
      </div>
    );
  }

  if (!user || user.role !== "admin") {
    return (
      <div className="page-view admin-page">
        <section className="empty-state admin-access-denied">
          <span className="empty-state-icon"><Icon name="lock" size={24} /></span>
          <h3>Нет доступа к администрированию</h3>
          <p>Этот раздел доступен только системному администратору Buscript.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="page-view admin-page">
      <header className="page-header admin-header">
        <div>
          <div className="page-title-meta">
            <span className="eyebrow">Системное администрирование</span>
            <span className="connection-badge is-live"><span className="status-dot" />Данные из БД</span>
          </div>
          <h1>Компании</h1>
          <p>Профили организаций и доступное количество уведомлений.</p>
        </div>
        <button className="button button-secondary" onClick={() => void loadTenants()} type="button">
          <Icon name="refresh" size={16} />Обновить
        </button>
      </header>

      {error && (
        <div className="profile-notice is-error" role="alert">
          <Icon name="alert" size={17} />{error}
        </div>
      )}
      {success && (
        <div className="profile-notice is-success" role="status">
          <Icon name="check" size={17} />{success}
        </div>
      )}

      <section className="admin-metric-line admin-real-metrics">
        <div><small>Всего компаний</small><strong>{tenants.length}</strong><span>зарегистрировано</span></div>
        <div><small>Активные компании</small><strong>{totals.active}</strong><span>доступ разрешён</span></div>
        <div><small>Всего заявок</small><strong>{totals.trips}</strong><span>во всех профилях</span></div>
        <div><small>Подано</small><strong>{totals.submitted}</strong><span>уведомлений</span></div>
        <div><small>Доступный лимит</small><strong>{totals.available}</strong><span>суммарно</span></div>
      </section>

      <section className="plain-section admin-tenants-section">
        <header className="plain-section-head admin-tenants-head">
          <div>
            <h3>Профили компаний</h3>
            <p>Изменение лимита создаёт реальную операцию в истории баланса.</p>
          </div>
          <label className="search-field">
            <Icon name="search" size={16} />
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Название, имя или email"
              type="search"
              value={query}
            />
          </label>
        </header>

        {filteredTenants.length === 0 ? (
          <div className="compact-empty admin-empty">
            <Icon name="admin" size={22} />
            <span>
              <strong>{tenants.length ? "Ничего не найдено" : "Компаний пока нет"}</strong>
              <small>{tenants.length ? "Измените поисковый запрос." : "Новые компании появятся после регистрации."}</small>
            </span>
          </div>
        ) : (
          <div className="admin-tenant-list">
            <div className="admin-tenant-head">
              <span>Компания</span>
              <span>Профиль</span>
              <span>Заявки</span>
              <span>Баланс уведомлений</span>
            </div>
            {filteredTenants.map((tenant) => {
              const draft = drafts[tenant.id] ?? {
                available: String(tenant.available),
                note: "",
              };
              const unchanged = Number(draft.available) === tenant.available && !draft.note.trim();
              return (
                <div className="admin-tenant-row" key={tenant.id}>
                  <div className="admin-tenant-identity">
                    <span className="company-avatar">{tenant.name.slice(0, 1).toUpperCase()}</span>
                    <span>
                      <strong>{tenant.name}</strong>
                      <small>
                        {tenant.primaryAdminFullName ?? "Администратор не указан"}
                        {tenant.primaryAdminEmail ? ` · ${tenant.primaryAdminEmail}` : ""}
                      </small>
                      <small>Создана {readableDate(tenant.createdAt)} · ID {tenant.id}</small>
                    </span>
                  </div>
                  <div className="admin-profile-state">
                    <span className={`status-badge ${tenant.onboardingCompleted ? "status-success" : "status-warning"}`}>
                      <span className="status-dot" />
                      {tenant.onboardingCompleted ? "Готов" : `${tenant.onboardingCompletionPercent}%`}
                    </span>
                    <small>{tenant.isActive ? "Доступ активен" : "Компания отключена"}</small>
                  </div>
                  <div className="admin-trip-count">
                    <strong>{tenant.tripsCount}</strong>
                    <small>{tenant.submittedTripsCount} подано</small>
                  </div>
                  <div className="admin-balance-editor">
                    <label>
                      <span>Доступно</span>
                      <input
                        inputMode="numeric"
                        min={0}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [tenant.id]: { ...draft, available: event.target.value },
                          }))
                        }
                        step={1}
                        type="number"
                        value={draft.available}
                      />
                    </label>
                    <label className="admin-balance-note">
                      <span>Комментарий</span>
                      <input
                        maxLength={2000}
                        onChange={(event) =>
                          setDrafts((current) => ({
                            ...current,
                            [tenant.id]: { ...draft, note: event.target.value },
                          }))
                        }
                        placeholder="Причина изменения"
                        value={draft.note}
                      />
                    </label>
                    <button
                      className="button button-dark button-small"
                      disabled={savingId === tenant.id || unchanged}
                      onClick={() => void saveBalance(tenant)}
                      type="button"
                    >
                      {savingId === tenant.id ? "Сохраняем…" : "Сохранить"}
                    </button>
                    {tenant.reserved > 0 && <small>{tenant.reserved} зарезервировано</small>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
