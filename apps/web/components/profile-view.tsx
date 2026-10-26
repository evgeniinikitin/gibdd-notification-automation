"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/icons";
import { ProgressBar } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type {
  BalanceSummary,
  CarrierProfile,
  Driver,
  DriverInput,
  OnboardingStatus,
  ProfileAsset,
  Trip,
  User,
  Vehicle,
  VehicleInput,
} from "@/lib/types";

type TabId = "account" | "carrier" | "fleet" | "balance";
type Notice = { tone: "success" | "error"; text: string } | null;

const tabs: Array<{ id: TabId; label: string }> = [
  { id: "account", label: "Профиль" },
  { id: "carrier", label: "Перевозчик" },
  { id: "fleet", label: "Автобусы и водители" },
  { id: "balance", label: "Баланс" },
];

const emptyCarrier: CarrierProfile = {
  legalName: "",
  inn: "",
  legalAddress: "",
  actualAddress: "",
  phone: "",
  email: "",
  directorPosition: "",
  directorFullName: "",
  licenseNumber: "",
  licenseIssuedAt: "",
};

const emptyVehicle: VehicleInput = {
  makeModel: "",
  registrationPlate: "",
  year: null,
  seats: 1,
  diagnosticCardNumber: "",
  diagnosticCardValidUntil: "",
  notes: "",
  isActive: true,
};

const emptyDriver: DriverInput = {
  fullName: "",
  birthDate: "",
  phone: "",
  licenseNumber: "",
  licenseCategories: "D",
  licenseIssuedAt: "",
  experienceFrom: "",
  notes: "",
  isActive: true,
};

const tripStatusLabels: Record<Trip["status"], string> = {
  draft: "Черновик",
  extracting: "Распознавание",
  clarifying: "Нужно уточнение",
  validated: "Проверено",
  generating_documents: "Формируем документы",
  dry_run_ready: "Готово к проверке",
  submitted: "Подано",
  completed: "Завершено",
  needs_support: "Нужна помощь",
};

function initials(value: string) {
  return value
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase() || "B";
}

function diagnosticExpired(value: string) {
  if (!value) return true;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return new Date(`${value}T00:00:00`) <= today;
}

function readableDate(value?: string) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ru", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(new Date(value));
}

function errorMessage(error: unknown) {
  return error instanceof ApiError ? error.message : "Не удалось сохранить изменения";
}

export function ProfileView() {
  const [tab, setTab] = useState<TabId>("account");
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [carrier, setCarrier] = useState<CarrierProfile>(emptyCarrier);
  const [assets, setAssets] = useState<ProfileAsset[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [balance, setBalance] = useState<BalanceSummary | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [accountDraft, setAccountDraft] = useState({ fullName: "", companyName: "" });
  const [vehicleDraft, setVehicleDraft] = useState<VehicleInput>(emptyVehicle);
  const [driverDraft, setDriverDraft] = useState<DriverInput>(emptyDriver);
  const [editingVehicleId, setEditingVehicleId] = useState<string | null>(null);
  const [editingDriverId, setEditingDriverId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [
        currentUser,
        onboardingStatus,
        carrierProfile,
        profileAssets,
        vehicleRows,
        driverRows,
        billing,
        tripRows,
      ] = await Promise.all([
        api.me(),
        api.onboarding(),
        api.carrier(),
        api.assets(),
        api.vehicles(),
        api.drivers(),
        api.billing(),
        api.trips(),
      ]);
      setUser(currentUser);
      setStatus(onboardingStatus);
      setCarrier(carrierProfile ?? emptyCarrier);
      setAssets(profileAssets);
      setVehicles(vehicleRows);
      setDrivers(driverRows);
      setBalance(billing);
      setTrips(tripRows);
      setAccountDraft({
        fullName: currentUser.fullName,
        companyName: currentUser.organizationName,
      });
    } catch (error) {
      setLoadError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const queryTab = new URLSearchParams(window.location.search).get("tab");
      if (tabs.some((item) => item.id === queryTab)) setTab(queryTab as TabId);
      void loadProfile();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadProfile]);

  function selectTab(nextTab: TabId) {
    setTab(nextTab);
    setNotice(null);
    const url = nextTab === "account" ? "/profile" : `/profile?tab=${nextTab}`;
    window.history.replaceState(null, "", url);
  }

  async function saveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving("account");
    setNotice(null);
    try {
      const updated = await api.updateAccount(accountDraft);
      setUser(updated);
      setAccountDraft({ fullName: updated.fullName, companyName: updated.organizationName });
      setNotice({ tone: "success", text: "Данные аккаунта сохранены" });
      window.dispatchEvent(new Event("buscript:account-updated"));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  async function saveCarrier(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving("carrier");
    setNotice(null);
    try {
      const updated = await api.updateCarrier(carrier);
      setCarrier(updated);
      setStatus(await api.onboarding());
      setNotice({ tone: "success", text: "Профиль перевозчика сохранён в базе данных" });
      window.dispatchEvent(new Event("buscript:account-updated"));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  async function uploadAsset(kind: "signature" | "stamp", file?: File) {
    if (!file) return;
    setSaving(kind);
    setNotice(null);
    try {
      await api.uploadAsset(kind, file);
      const [nextAssets, nextStatus] = await Promise.all([api.assets(), api.onboarding()]);
      setAssets(nextAssets);
      setStatus(nextStatus);
      setNotice({
        tone: "success",
        text: kind === "signature" ? "Подпись загружена" : "Печать загружена",
      });
      window.dispatchEvent(new Event("buscript:account-updated"));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  async function saveVehicle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving("vehicle");
    setNotice(null);
    try {
      if (editingVehicleId) {
        await api.updateVehicle(editingVehicleId, vehicleDraft);
      } else {
        await api.createVehicle(vehicleDraft);
      }
      const [nextVehicles, nextStatus] = await Promise.all([api.vehicles(), api.onboarding()]);
      setVehicles(nextVehicles);
      setStatus(nextStatus);
      setVehicleDraft(emptyVehicle);
      setEditingVehicleId(null);
      setNotice({
        tone: "success",
        text: editingVehicleId ? "Автобус обновлён" : "Автобус добавлен",
      });
      window.dispatchEvent(new Event("buscript:account-updated"));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  function editVehicle(vehicle: Vehicle) {
    setEditingVehicleId(vehicle.id);
    setVehicleDraft({
      makeModel: vehicle.makeModel,
      registrationPlate: vehicle.registrationPlate,
      year: vehicle.year,
      seats: vehicle.seats,
      diagnosticCardNumber: vehicle.diagnosticCardNumber,
      diagnosticCardValidUntil: vehicle.diagnosticCardValidUntil,
      notes: vehicle.notes,
      isActive: vehicle.isActive,
    });
    setNotice(null);
  }

  async function archiveVehicle(vehicle: Vehicle) {
    if (!window.confirm(`Архивировать автобус ${vehicle.registrationPlate}?`)) return;
    setSaving(`vehicle-${vehicle.id}`);
    setNotice(null);
    try {
      await api.deleteVehicle(vehicle.id);
      setVehicles(await api.vehicles());
      setStatus(await api.onboarding());
      setNotice({ tone: "success", text: "Автобус перенесён в архив" });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  async function saveDriver(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving("driver");
    setNotice(null);
    try {
      if (editingDriverId) {
        await api.updateDriver(editingDriverId, driverDraft);
      } else {
        await api.createDriver(driverDraft);
      }
      const [nextDrivers, nextStatus] = await Promise.all([api.drivers(), api.onboarding()]);
      setDrivers(nextDrivers);
      setStatus(nextStatus);
      setDriverDraft(emptyDriver);
      setEditingDriverId(null);
      setNotice({
        tone: "success",
        text: editingDriverId ? "Водитель обновлён" : "Водитель добавлен",
      });
      window.dispatchEvent(new Event("buscript:account-updated"));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  function editDriver(driver: Driver) {
    setEditingDriverId(driver.id);
    setDriverDraft({
      fullName: driver.fullName,
      birthDate: driver.birthDate,
      phone: driver.phone,
      licenseNumber: driver.licenseNumber,
      licenseCategories: driver.licenseCategories,
      licenseIssuedAt: driver.licenseIssuedAt,
      experienceFrom: driver.experienceFrom,
      notes: driver.notes,
      isActive: driver.isActive,
    });
    setNotice(null);
  }

  async function archiveDriver(driver: Driver) {
    if (!window.confirm(`Архивировать водителя ${driver.fullName}?`)) return;
    setSaving(`driver-${driver.id}`);
    setNotice(null);
    try {
      await api.deleteDriver(driver.id);
      setDrivers(await api.drivers());
      setStatus(await api.onboarding());
      setNotice({ tone: "success", text: "Водитель перенесён в архив" });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setSaving(null);
    }
  }

  if (loading) {
    return (
      <div className="page-view profile-page">
        <div className="profile-load-state"><span className="shell-spinner" />Загружаем профиль…</div>
      </div>
    );
  }

  if (loadError || !user) {
    return (
      <div className="page-view profile-page">
        <section className="empty-state profile-error-state">
          <span className="empty-state-icon"><Icon name="alert" size={24} /></span>
          <h3>Профиль не загрузился</h3>
          <p>{loadError ?? "Данные аккаунта недоступны"}</p>
          <button className="button button-dark" onClick={() => void loadProfile()} type="button">
            Повторить
          </button>
        </section>
      </div>
    );
  }

  const canManage = user.role !== "client_user";
  const signature = assets.find((item) => item.kind === "signature");
  const stamp = assets.find((item) => item.kind === "stamp");
  const submittedTrips = trips.filter((trip) => trip.status === "submitted" || trip.status === "completed");
  const activeTrips = trips.filter(
    (trip) => !["submitted", "completed"].includes(trip.status),
  );
  const balanceTotal = balance ? balance.available + balance.reserved : 0;
  const balanceProgress =
    balance && balanceTotal > 0 ? (balance.available / balanceTotal) * 360 : 0;

  return (
    <div className="page-view profile-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Настройки</span>
          <h1>Профиль</h1>
          <p>Все изменения сохраняются в рабочем профиле компании.</p>
        </div>
        {status && (
          <span className={`status-badge ${status.completed ? "status-success" : "status-warning"}`}>
            <span className="status-dot" />
            {status.completed ? "Готов к работе" : `Заполнено на ${status.completionPercent}%`}
          </span>
        )}
      </header>

      <div className="profile-tabs" role="tablist">
        {tabs.map((item) => (
          <button
            aria-selected={tab === item.id}
            className={tab === item.id ? "is-active" : ""}
            key={item.id}
            onClick={() => selectTab(item.id)}
            role="tab"
            type="button"
          >
            {item.label}
            {item.id === "carrier" && status && !status.completed && <span className="tab-alert" />}
          </button>
        ))}
      </div>

      {notice && (
        <div className={`profile-notice is-${notice.tone}`} role="status">
          <Icon name={notice.tone === "success" ? "check" : "alert"} size={17} />
          {notice.text}
        </div>
      )}

      {tab === "account" && (
        <div className="profile-content animate-in">
          <section className="profile-summary">
            <div className="company-identity">
              <span className="company-large-avatar">{initials(user.organizationName)}</span>
              <div>
                <span className="status-badge status-blue"><span className="status-dot" />Активный аккаунт</span>
                <h2>{user.organizationName}</h2>
                <p>{user.email} · ID {user.organizationId}</p>
              </div>
            </div>
            {status && (
              <div className="profile-completion">
                <div><span>Заполненность рабочего профиля</span><strong>{status.completionPercent}%</strong></div>
                <ProgressBar value={status.completionPercent} />
                <small>
                  {status.completed
                    ? "Обязательные данные заполнены."
                    : `Осталось: ${status.missingFields.join(", ")}.`}
                </small>
              </div>
            )}
          </section>

          <div className="profile-columns">
            <form className="plain-section settings-form" onSubmit={saveAccount}>
              <header className="plain-section-head">
                <div>
                  <h3>Данные аккаунта</h3>
                  <p>Название в интерфейсе и имя владельца аккаунта.</p>
                </div>
              </header>
              <div className="settings-field-grid">
                <label className="field-group">
                  <span>Название компании</span>
                  <span className="input-shell">
                    <input
                      disabled={!canManage}
                      minLength={2}
                      onChange={(event) => setAccountDraft((value) => ({ ...value, companyName: event.target.value }))}
                      required
                      value={accountDraft.companyName}
                    />
                  </span>
                </label>
                <label className="field-group">
                  <span>Имя пользователя</span>
                  <span className="input-shell">
                    <input
                      disabled={!canManage}
                      minLength={2}
                      onChange={(event) => setAccountDraft((value) => ({ ...value, fullName: event.target.value }))}
                      required
                      value={accountDraft.fullName}
                    />
                  </span>
                </label>
                <label className="field-group settings-field-wide">
                  <span>Электронная почта</span>
                  <span className="input-shell is-readonly"><input readOnly value={user.email} /></span>
                  <small className="field-hint">Почта используется для входа и сейчас не изменяется.</small>
                </label>
              </div>
              <div className="settings-actions">
                <button className="button button-dark" disabled={!canManage || saving === "account"} type="submit">
                  {saving === "account" ? "Сохраняем…" : "Сохранить профиль"}
                </button>
              </div>
            </form>

            <section className="plain-section">
              <header className="plain-section-head">
                <div><h3>Активность поездок</h3><p>Только реальные заявки этой компании.</p></div>
              </header>
              <div className="profile-trip-metrics">
                <div><strong>{trips.length}</strong><span>всего заявок</span></div>
                <div><strong>{submittedTrips.length}</strong><span>подано</span></div>
                <div><strong>{activeTrips.length}</strong><span>в работе</span></div>
              </div>
              <div className="profile-trip-list">
                {submittedTrips.length === 0 ? (
                  <div className="compact-empty">
                    <Icon name="document" size={20} />
                    <span><strong>Поданных поездок пока нет</strong><small>Они появятся здесь после подачи уведомления.</small></span>
                  </div>
                ) : (
                  submittedTrips.slice(0, 5).map((trip) => (
                    <div key={trip.id}>
                      <span><strong>{trip.reference}</strong><small>{trip.customer}</small></span>
                      <span><strong>{trip.date}</strong><small>{tripStatusLabels[trip.status]}</small></span>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        </div>
      )}

      {tab === "carrier" && (
        <div className="profile-content animate-in">
          {!canManage && (
            <div className="profile-notice is-error">
              <Icon name="lock" size={17} />Изменять данные может администратор компании.
            </div>
          )}
          <form className="plain-section settings-form" onSubmit={saveCarrier}>
            <header className="plain-section-head">
              <div><h3>Реквизиты перевозчика</h3><p>Эти данные используются во всех уведомлениях компании.</p></div>
            </header>
            <div className="settings-field-grid settings-field-grid-three">
              <label className="field-group settings-field-wide">
                <span>Полное наименование</span>
                <span className="input-shell"><input disabled={!canManage} minLength={2} onChange={(event) => setCarrier((value) => ({ ...value, legalName: event.target.value }))} required value={carrier.legalName} /></span>
              </label>
              <label className="field-group">
                <span>ИНН</span>
                <span className="input-shell"><input disabled={!canManage} inputMode="numeric" pattern="\d{10}(\d{2})?" onChange={(event) => setCarrier((value) => ({ ...value, inn: event.target.value }))} required value={carrier.inn} /></span>
              </label>
              <label className="field-group">
                <span>Телефон</span>
                <span className="input-shell"><input disabled={!canManage} minLength={5} onChange={(event) => setCarrier((value) => ({ ...value, phone: event.target.value }))} required type="tel" value={carrier.phone} /></span>
              </label>
              <label className="field-group">
                <span>Электронная почта</span>
                <span className="input-shell"><input disabled={!canManage} onChange={(event) => setCarrier((value) => ({ ...value, email: event.target.value }))} required type="email" value={carrier.email} /></span>
              </label>
              <label className="field-group settings-field-wide">
                <span>Юридический адрес</span>
                <span className="input-shell"><input disabled={!canManage} minLength={5} onChange={(event) => setCarrier((value) => ({ ...value, legalAddress: event.target.value }))} required value={carrier.legalAddress} /></span>
              </label>
              <label className="field-group settings-field-wide">
                <span>Фактический адрес <em>необязательно</em></span>
                <span className="input-shell"><input disabled={!canManage} onChange={(event) => setCarrier((value) => ({ ...value, actualAddress: event.target.value }))} value={carrier.actualAddress} /></span>
              </label>
            </div>

            <div className="settings-form-divider">
              <strong>Ответственный подписант</strong>
            </div>
            <div className="settings-field-grid settings-field-grid-three">
              <label className="field-group">
                <span>ФИО</span>
                <span className="input-shell"><input disabled={!canManage} minLength={2} onChange={(event) => setCarrier((value) => ({ ...value, directorFullName: event.target.value }))} required value={carrier.directorFullName} /></span>
              </label>
              <label className="field-group">
                <span>Должность</span>
                <span className="input-shell"><input disabled={!canManage} minLength={2} onChange={(event) => setCarrier((value) => ({ ...value, directorPosition: event.target.value }))} required value={carrier.directorPosition} /></span>
              </label>
              <label className="field-group">
                <span>Номер лицензии <em>необязательно</em></span>
                <span className="input-shell"><input disabled={!canManage} onChange={(event) => setCarrier((value) => ({ ...value, licenseNumber: event.target.value }))} value={carrier.licenseNumber} /></span>
              </label>
              <label className="field-group">
                <span>Дата выдачи лицензии <em>необязательно</em></span>
                <span className="input-shell"><input disabled={!canManage} onChange={(event) => setCarrier((value) => ({ ...value, licenseIssuedAt: event.target.value }))} type="date" value={carrier.licenseIssuedAt} /></span>
              </label>
            </div>
            <div className="settings-actions">
              <button className="button button-dark" disabled={!canManage || saving === "carrier"} type="submit">
                {saving === "carrier" ? "Сохраняем…" : "Сохранить реквизиты"}
              </button>
              {carrier.updatedAt && <small>Последнее изменение: {readableDate(carrier.updatedAt)}</small>}
            </div>
          </form>

          <section className="plain-section">
            <header className="plain-section-head">
              <div><h3>Подпись и печать</h3><p>Прозрачные PNG-файлы. Подпись обязательна, печать — по желанию.</p></div>
            </header>
            <div className="profile-assets-grid">
              <div className={signature ? "is-ready" : ""}>
                <span className="asset-icon"><Icon name="signature" size={24} /></span>
                <span>
                  <strong>Подпись</strong>
                  <small>{signature ? `Загружена ${readableDate(signature.createdAt)}` : "Файл ещё не загружен"}</small>
                </span>
                <label className="button button-secondary button-small">
                  {saving === "signature" ? "Загрузка…" : signature ? "Заменить" : "Загрузить PNG"}
                  <input accept="image/png,.png" disabled={!canManage || saving === "signature"} onChange={(event) => void uploadAsset("signature", event.target.files?.[0])} type="file" />
                </label>
              </div>
              <div className={stamp ? "is-ready" : ""}>
                <span className="asset-icon"><Icon name="document" size={24} /></span>
                <span>
                  <strong>Печать</strong>
                  <small>{stamp ? `Загружена ${readableDate(stamp.createdAt)}` : "Необязательный файл"}</small>
                </span>
                <label className="button button-secondary button-small">
                  {saving === "stamp" ? "Загрузка…" : stamp ? "Заменить" : "Загрузить PNG"}
                  <input accept="image/png,.png" disabled={!canManage || saving === "stamp"} onChange={(event) => void uploadAsset("stamp", event.target.files?.[0])} type="file" />
                </label>
              </div>
            </div>
          </section>
        </div>
      )}

      {tab === "fleet" && (
        <div className="profile-content animate-in">
          <div className="fleet-settings-grid">
            <section className="plain-section">
              <header className="plain-section-head">
                <div><h3>Автобусы</h3><p>{vehicles.length ? `${vehicles.length} активных записей` : "Справочник пока пуст"}</p></div>
              </header>
              <div className="profile-entity-list">
                {vehicles.length === 0 ? (
                  <div className="compact-empty"><Icon name="bus" size={20} /><span><strong>Нет автобусов</strong><small>Добавьте первый автобус в форме ниже.</small></span></div>
                ) : vehicles.map((vehicle) => {
                  const expired = diagnosticExpired(vehicle.diagnosticCardValidUntil);
                  return (
                  <div className={expired ? "entity-invalid" : undefined} key={vehicle.id}>
                    <span className="entity-avatar"><Icon name="bus" size={17} /></span>
                    <span><strong>{vehicle.makeModel}</strong><small>{vehicle.registrationPlate} · {vehicle.seats} мест</small>{expired && <em>Диагностическая карта истекла</em>}</span>
                    <div className="entity-actions">
                      <button className="text-button" onClick={() => editVehicle(vehicle)} type="button">Изменить</button>
                      <button className="text-button text-danger" disabled={saving === `vehicle-${vehicle.id}`} onClick={() => void archiveVehicle(vehicle)} type="button">Архивировать</button>
                    </div>
                  </div>
                  );
                })}
              </div>
            </section>

            <form className="plain-section settings-form compact-settings-form" onSubmit={saveVehicle}>
              <header className="plain-section-head">
                <div><h3>{editingVehicleId ? "Редактировать автобус" : "Добавить автобус"}</h3><p>Все поля сохраняются в справочнике компании.</p></div>
                {editingVehicleId && <button className="text-button" onClick={() => { setEditingVehicleId(null); setVehicleDraft(emptyVehicle); }} type="button">Отменить</button>}
              </header>
              <div className="settings-field-grid">
                <label className="field-group settings-field-wide"><span>Марка и модель</span><span className="input-shell"><input disabled={!canManage} minLength={2} onChange={(event) => setVehicleDraft((value) => ({ ...value, makeModel: event.target.value }))} required value={vehicleDraft.makeModel} /></span></label>
                <label className="field-group"><span>Госномер</span><span className="input-shell"><input disabled={!canManage} minLength={5} onChange={(event) => setVehicleDraft((value) => ({ ...value, registrationPlate: event.target.value.toUpperCase() }))} required value={vehicleDraft.registrationPlate} /></span></label>
                <label className="field-group"><span>Количество мест</span><span className="input-shell"><input disabled={!canManage} max={120} min={1} onChange={(event) => setVehicleDraft((value) => ({ ...value, seats: Number(event.target.value) }))} required type="number" value={vehicleDraft.seats} /></span></label>
                <label className="field-group"><span>Год выпуска</span><span className="input-shell"><input disabled={!canManage} max={2100} min={1950} onChange={(event) => setVehicleDraft((value) => ({ ...value, year: event.target.value ? Number(event.target.value) : null }))} type="number" value={vehicleDraft.year ?? ""} /></span></label>
                <label className="field-group"><span>Диагностическая карта</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setVehicleDraft((value) => ({ ...value, diagnosticCardNumber: event.target.value }))} value={vehicleDraft.diagnosticCardNumber} /></span></label>
                <label className="field-group"><span>Карта действует до</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setVehicleDraft((value) => ({ ...value, diagnosticCardValidUntil: event.target.value }))} type="date" value={vehicleDraft.diagnosticCardValidUntil} /></span></label>
              </div>
              <div className="settings-actions"><button className="button button-dark" disabled={!canManage || saving === "vehicle"} type="submit">{saving === "vehicle" ? "Сохраняем…" : editingVehicleId ? "Сохранить автобус" : "Добавить автобус"}</button></div>
            </form>
          </div>

          <div className="fleet-settings-grid">
            <section className="plain-section">
              <header className="plain-section-head">
                <div><h3>Водители</h3><p>{drivers.length ? `${drivers.length} активных записей` : "Справочник пока пуст"}</p></div>
              </header>
              <div className="profile-entity-list">
                {drivers.length === 0 ? (
                  <div className="compact-empty"><Icon name="profile" size={20} /><span><strong>Нет водителей</strong><small>Добавьте первого водителя в форме ниже.</small></span></div>
                ) : drivers.map((driver) => (
                  <div key={driver.id}>
                    <span className="entity-avatar"><Icon name="profile" size={17} /></span>
                    <span><strong>{driver.fullName}</strong><small>Удостоверение {driver.licenseNumber} · {driver.licenseCategories}</small></span>
                    <div className="entity-actions">
                      <button className="text-button" onClick={() => editDriver(driver)} type="button">Изменить</button>
                      <button className="text-button text-danger" disabled={saving === `driver-${driver.id}`} onClick={() => void archiveDriver(driver)} type="button">Архивировать</button>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            <form className="plain-section settings-form compact-settings-form" onSubmit={saveDriver}>
              <header className="plain-section-head">
                <div><h3>{editingDriverId ? "Редактировать водителя" : "Добавить водителя"}</h3><p>Данные водительского удостоверения.</p></div>
                {editingDriverId && <button className="text-button" onClick={() => { setEditingDriverId(null); setDriverDraft(emptyDriver); }} type="button">Отменить</button>}
              </header>
              <div className="settings-field-grid">
                <label className="field-group settings-field-wide"><span>ФИО</span><span className="input-shell"><input disabled={!canManage} minLength={2} onChange={(event) => setDriverDraft((value) => ({ ...value, fullName: event.target.value }))} required value={driverDraft.fullName} /></span></label>
                <label className="field-group"><span>Номер удостоверения</span><span className="input-shell"><input disabled={!canManage} minLength={3} onChange={(event) => setDriverDraft((value) => ({ ...value, licenseNumber: event.target.value }))} required value={driverDraft.licenseNumber} /></span></label>
                <label className="field-group"><span>Категории</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setDriverDraft((value) => ({ ...value, licenseCategories: event.target.value }))} required value={driverDraft.licenseCategories} /></span></label>
                <label className="field-group"><span>Телефон</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setDriverDraft((value) => ({ ...value, phone: event.target.value }))} type="tel" value={driverDraft.phone} /></span></label>
                <label className="field-group"><span>Дата рождения</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setDriverDraft((value) => ({ ...value, birthDate: event.target.value }))} type="date" value={driverDraft.birthDate} /></span></label>
                <label className="field-group"><span>Стаж с</span><span className="input-shell"><input disabled={!canManage} onChange={(event) => setDriverDraft((value) => ({ ...value, experienceFrom: event.target.value }))} type="date" value={driverDraft.experienceFrom} /></span></label>
              </div>
              <div className="settings-actions"><button className="button button-dark" disabled={!canManage || saving === "driver"} type="submit">{saving === "driver" ? "Сохраняем…" : editingDriverId ? "Сохранить водителя" : "Добавить водителя"}</button></div>
            </form>
          </div>
        </div>
      )}

      {tab === "balance" && (
        <div className="profile-content balance-content animate-in">
          {balance ? (
            <>
              <section className="balance-hero">
                <div>
                  <span className="eyebrow">Доступно</span>
                  <strong>{balance.available}</strong>
                  <p>уведомлений можно запустить сейчас</p>
                </div>
                <div className="balance-visual">
                  <span style={{ "--balance-progress": `${balanceProgress}deg` } as React.CSSProperties}>
                    <strong>{balanceTotal > 0 ? Math.round((balance.available / balanceTotal) * 100) : 0}%</strong>
                    <small>свободно</small>
                  </span>
                </div>
                <div className="balance-breakdown">
                  <div><span>Доступно</span><strong>{balance.available}</strong></div>
                  <div><span>Зарезервировано</span><strong>{balance.reserved}</strong></div>
                  <div><span>Всего сейчас</span><strong>{balanceTotal}</strong></div>
                </div>
              </section>

              <section className="plain-section transactions-section">
                <header className="plain-section-head">
                  <div><h3>История операций</h3><p>Реальные начисления, резервы и списания.</p></div>
                </header>
                <div className="transaction-list">
                  {balance.transactions.length === 0 ? (
                    <div className="compact-empty"><Icon name="balance" size={20} /><span><strong>Операций пока нет</strong><small>История появится после начисления или подачи.</small></span></div>
                  ) : balance.transactions.map((transaction) => (
                    <div key={transaction.id}>
                      <span className={`transaction-icon ${transaction.amount > 0 ? "is-plus" : ""}`}><Icon name={transaction.amount > 0 ? "plus" : "document"} size={17} /></span>
                      <span><strong>{transaction.label}</strong><small>{transaction.createdAt} · {transaction.actor}</small></span>
                      <strong className={transaction.amount > 0 ? "amount-plus" : ""}>{transaction.amount > 0 ? "+" : ""}{transaction.amount}</strong>
                    </div>
                  ))}
                </div>
              </section>
            </>
          ) : (
            <section className="empty-state"><h3>Баланс недоступен</h3><p>Повторите загрузку страницы.</p></section>
          )}
        </div>
      )}
    </div>
  );
}
