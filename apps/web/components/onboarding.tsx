"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Icon, type IconName } from "@/components/icons";
import { ProgressBar } from "@/components/ui";
import { api, ApiError, clearTokens } from "@/lib/api";
import type {
  CarrierProfile,
  Driver,
  DriverInput,
  OnboardingStepId,
  Vehicle,
  VehicleInput,
} from "@/lib/types";

type Step = {
  id: OnboardingStepId;
  title: string;
  short: string;
  description: string;
  icon: IconName;
};

const steps: Step[] = [
  {
    id: "company",
    title: "Реквизиты перевозчика",
    short: "Реквизиты",
    description: "Юридические и контактные данные",
    icon: "home",
  },
  {
    id: "signer",
    title: "Подписант",
    short: "Подписант",
    description: "Ответственное лицо и должность",
    icon: "profile",
  },
  {
    id: "assets",
    title: "Подпись и печать",
    short: "Подпись и печать",
    description: "PNG-файлы на прозрачном фоне",
    icon: "signature",
  },
  {
    id: "fleet",
    title: "Автобусы и водители",
    short: "Автобусы и водители",
    description: "Транспорт и диагностические карты",
    icon: "bus",
  },
  {
    id: "review",
    title: "Проверка профиля",
    short: "Проверка",
    description: "Итоговая готовность профиля",
    icon: "check",
  },
];

type ProfileData = {
  legalName: string;
  inn: string;
  legalAddress: string;
  email: string;
  phone: string;
  signerName: string;
  signerPosition: string;
};

const initialProfile: ProfileData = {
  legalName: "",
  inn: "",
  legalAddress: "",
  email: "",
  phone: "",
  signerName: "",
  signerPosition: "",
};

type VehicleFormData = {
  makeModel: string;
  registrationPlate: string;
  year: string;
  seats: string;
  diagnosticCardNumber: string;
  diagnosticCardValidUntil: string;
  notes: string;
};

type DriverFormData = {
  fullName: string;
  birthDate: string;
  phone: string;
  licenseNumber: string;
  licenseCategories: string;
  licenseIssuedAt: string;
  experienceFrom: string;
  notes: string;
};

const initialVehicle: VehicleFormData = {
  makeModel: "",
  registrationPlate: "",
  year: "",
  seats: "55",
  diagnosticCardNumber: "",
  diagnosticCardValidUntil: "",
  notes: "",
};

const initialDriver: DriverFormData = {
  fullName: "",
  birthDate: "",
  phone: "",
  licenseNumber: "",
  licenseCategories: "D",
  licenseIssuedAt: "",
  experienceFrom: "",
  notes: "",
};

let onboardingSnapshotPromise: Promise<{
  user: Awaited<ReturnType<typeof api.me>>;
  carrier: Awaited<ReturnType<typeof api.carrier>>;
  status: Awaited<ReturnType<typeof api.onboarding>>;
  assets: Awaited<ReturnType<typeof api.assets>>;
  storedVehicles: Awaited<ReturnType<typeof api.vehicles>>;
  storedDrivers: Awaited<ReturnType<typeof api.drivers>>;
}> | null = null;

function waitForRetry(delay: number) {
  return new Promise((resolve) => window.setTimeout(resolve, delay));
}

async function loadOnboardingSnapshot() {
  if (onboardingSnapshotPromise) return onboardingSnapshotPromise;

  const load = (async () => {
    let lastError: unknown;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const [user, carrier, status, assets, storedVehicles, storedDrivers] =
          await Promise.all([
            api.me(),
            api.carrier(),
            api.onboarding(),
            api.assets(),
            api.vehicles(),
            api.drivers(),
          ]);
        return { user, carrier, status, assets, storedVehicles, storedDrivers };
      } catch (error) {
        lastError = error;
        if (error instanceof ApiError && error.status === 401) throw error;
        if (attempt < 2) await waitForRetry(250 * 2 ** attempt);
      }
    }
    throw lastError;
  })();

  onboardingSnapshotPromise = load;
  void load.then(
    () => {
      if (onboardingSnapshotPromise === load) onboardingSnapshotPromise = null;
    },
    () => {
      if (onboardingSnapshotPromise === load) onboardingSnapshotPromise = null;
    },
  );
  return load;
}

function profileFromCarrier(carrier: CarrierProfile | null): ProfileData {
  return {
    legalName: carrier?.legalName ?? "",
    inn: carrier?.inn ?? "",
    legalAddress: carrier?.legalAddress ?? "",
    email: carrier?.email ?? "",
    phone: carrier?.phone ?? "",
    signerName: carrier?.directorFullName ?? "",
    signerPosition: carrier?.directorPosition ?? "",
  };
}

function hasCompanyData(profile: ProfileData) {
  return Boolean(
    profile.legalName &&
      profile.inn &&
      profile.legalAddress &&
      profile.email &&
      profile.phone,
  );
}

function hasSignerData(profile: ProfileData) {
  return Boolean(profile.signerName && profile.signerPosition);
}

function vehicleFormFromRecord(record: Vehicle): VehicleFormData {
  return {
    makeModel: record.makeModel,
    registrationPlate: record.registrationPlate,
    year: record.year ? String(record.year) : "",
    seats: String(record.seats),
    diagnosticCardNumber: record.diagnosticCardNumber,
    diagnosticCardValidUntil: record.diagnosticCardValidUntil,
    notes: record.notes,
  };
}

function driverFormFromRecord(record: Driver): DriverFormData {
  return {
    fullName: record.fullName,
    birthDate: record.birthDate,
    phone: record.phone,
    licenseNumber: record.licenseNumber,
    licenseCategories: record.licenseCategories,
    licenseIssuedAt: record.licenseIssuedAt,
    experienceFrom: record.experienceFrom,
    notes: record.notes,
  };
}

function OnboardingField({
  label,
  required,
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field-group onboarding-field">
      <span>
        {label}
        {required && <em>*</em>}
      </span>
      {children}
      {hint && <small className="field-hint">{hint}</small>}
    </label>
  );
}

function FileDrop({
  label,
  description,
  accept,
  file,
  stored,
  onFile,
}: {
  label: string;
  description: string;
  accept: string;
  file: File | null;
  stored?: boolean;
  onFile: (file: File | null) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const ready = Boolean(file || stored);
  return (
    <div className={`asset-drop ${ready ? "has-file" : ""}`}>
      <input
        accept={accept}
        hidden
        onChange={(event) => onFile(event.target.files?.[0] ?? null)}
        ref={inputRef}
        type="file"
      />
      <span className="asset-drop-icon">
        <Icon name={ready ? "check" : "upload"} size={20} />
      </span>
      <div>
        <strong>{file?.name ?? (stored ? `${label} загружена` : label)}</strong>
        <small>
          {file
            ? `${Math.round(file.size / 1000)} КБ · будет сохранено после продолжения`
            : stored
              ? "Сохранено в профиле · можно заменить"
              : description}
        </small>
      </div>
      <button
        className="button button-secondary button-small"
        onClick={() => (file ? onFile(null) : inputRef.current?.click())}
        type="button"
      >
        {file ? "Отменить" : stored ? "Заменить" : "Выбрать файл"}
      </button>
    </div>
  );
}

export function Onboarding() {
  const router = useRouter();
  const [authState, setAuthState] = useState<"checking" | "ready" | "unavailable">(
    "checking",
  );
  const [loadError, setLoadError] = useState(
    "Не удалось загрузить профиль из базы данных.",
  );
  const [organizationName, setOrganizationName] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [visitedSteps, setVisitedSteps] = useState<Set<OnboardingStepId>>(
    () => new Set(["company"]),
  );
  const [profile, setProfile] = useState<ProfileData>(initialProfile);
  const [companySaved, setCompanySaved] = useState(false);
  const [signerSaved, setSignerSaved] = useState(false);
  const [signature, setSignature] = useState<File | null>(null);
  const [stamp, setStamp] = useState<File | null>(null);
  const [signatureStored, setSignatureStored] = useState(false);
  const [stampStored, setStampStored] = useState(false);
  const [usesStamp, setUsesStamp] = useState(false);
  const [vehicleEntries, setVehicleEntries] = useState<Vehicle[]>([]);
  const [driverEntries, setDriverEntries] = useState<Driver[]>([]);
  const [vehicle, setVehicle] = useState<VehicleFormData>(initialVehicle);
  const [driver, setDriver] = useState<DriverFormData>(initialDriver);
  const [vehicleFormOpen, setVehicleFormOpen] = useState(false);
  const [driverFormOpen, setDriverFormOpen] = useState(false);
  const [editingVehicleId, setEditingVehicleId] = useState<string | null>(null);
  const [editingDriverId, setEditingDriverId] = useState<string | null>(null);
  const [fleetSaving, setFleetSaving] = useState<"vehicle" | "driver" | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    void loadOnboardingSnapshot()
      .then(({ user, carrier, status, assets, storedVehicles, storedDrivers }) => {
        if (cancelled) return;
        if (user.role === "admin") {
          router.replace("/admin");
          return;
        }
        const hydratedProfile = profileFromCarrier(carrier);
        const hasSignature = assets.some((asset) => asset.kind === "signature");
        const hasStamp = assets.some((asset) => asset.kind === "stamp");
        const companyReady = hasCompanyData(hydratedProfile);
        const signerReady = hasSignerData(hydratedProfile);
        const fleetReady = storedVehicles.length > 0 && storedDrivers.length > 0;
        const firstIncomplete = !companyReady
          ? 0
          : !signerReady
            ? 1
            : !hasSignature
              ? 2
              : !fleetReady
                ? 3
                : 4;

        setOrganizationName(carrier?.legalName || user.organizationName);
        setProfile(hydratedProfile);
        setCompanySaved(companyReady);
        setSignerSaved(signerReady);
        setSignatureStored(hasSignature);
        setStampStored(hasStamp);
        setUsesStamp(hasStamp);
        setVehicleEntries(storedVehicles);
        setDriverEntries(storedDrivers);
        setActiveIndex(status.completed ? 4 : firstIncomplete);
        setVisitedSteps(() => {
          const hydrated = new Set<OnboardingStepId>(status.completedSteps);
          hydrated.add(steps[status.completed ? 4 : firstIncomplete].id);
          return hydrated;
        });
        setAuthState("ready");
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          clearTokens();
          router.replace("/login");
          return;
        }
        setLoadError(
          error instanceof ApiError
            ? error.message
            : "Не удалось загрузить профиль из базы данных.",
        );
        setAuthState("unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  const activeStep = steps[activeIndex];
  const progress = Math.round(((activeIndex + 1) / steps.length) * 100);

  const completion = useMemo(() => {
    const sections = [
      companySaved,
      signerSaved,
      signatureStored,
      vehicleEntries.length > 0 && driverEntries.length > 0,
    ];
    return {
      count: sections.filter(Boolean).length,
      total: sections.length,
      ready: sections.every(Boolean),
    };
  }, [
    companySaved,
    driverEntries.length,
    signatureStored,
    signerSaved,
    vehicleEntries.length,
  ]);

  const stepCompletion = useMemo<Record<OnboardingStepId, boolean>>(
    () => ({
      company: companySaved,
      signer: signerSaved,
      assets: signatureStored,
      fleet: vehicleEntries.length > 0 && driverEntries.length > 0,
      review: completion.ready,
    }),
    [
      companySaved,
      completion.ready,
      driverEntries.length,
      signatureStored,
      signerSaved,
      vehicleEntries.length,
    ],
  );

  function setField(field: keyof ProfileData) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      setProfile((current) => ({ ...current, [field]: event.target.value }));
      if (
        field === "legalName" ||
        field === "inn" ||
        field === "legalAddress" ||
        field === "email" ||
        field === "phone"
      ) {
        setCompanySaved(false);
      } else {
        setSignerSaved(false);
      }
      setErrors([]);
    };
  }

  function goTo(index: number) {
    setVisitedSteps((current) => {
      const next = new Set(current);
      next.add(activeStep.id);
      next.add(steps[index].id);
      return next;
    });
    setActiveIndex(index);
    setErrors([]);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showRequestError(error: unknown) {
    if (error instanceof ApiError && error.status === 401) {
      clearTokens();
      router.replace("/login");
      return;
    }
    setErrors([
      error instanceof ApiError
        ? error.message
        : "Не удалось сохранить данные. Повторите попытку.",
    ]);
  }

  function validateCurrent() {
    const nextErrors: string[] = [];
    if (activeStep.id === "company") {
      if (!profile.legalName) nextErrors.push("Укажите полное наименование перевозчика");
      if (!profile.inn) nextErrors.push("Укажите ИНН");
      if (!profile.legalAddress) nextErrors.push("Укажите юридический адрес");
      if (!profile.email) nextErrors.push("Укажите рабочую электронную почту");
      if (!profile.phone) nextErrors.push("Укажите телефон");
    }
    if (activeStep.id === "signer") {
      if (!profile.signerName) nextErrors.push("Укажите ФИО подписанта");
      if (!profile.signerPosition) nextErrors.push("Укажите должность подписанта");
    }
    if (activeStep.id === "assets" && !signatureStored && !signature) {
      nextErrors.push("Загрузите PNG-файл подписи");
    }
    if (
      activeStep.id === "fleet" &&
      (vehicleEntries.length === 0 || driverEntries.length === 0)
    ) {
      nextErrors.push("Добавьте хотя бы один автобус и одного водителя");
    }
    if (activeStep.id === "fleet" && (vehicleFormOpen || driverFormOpen)) {
      nextErrors.push("Сохраните или отмените открытую форму перед продолжением");
    }
    setErrors(nextErrors);
    return nextErrors.length === 0;
  }

  function openNewVehicle() {
    setVehicle(initialVehicle);
    setEditingVehicleId(null);
    setVehicleFormOpen(true);
    setErrors([]);
  }

  function openVehicle(record: Vehicle) {
    setVehicle(vehicleFormFromRecord(record));
    setEditingVehicleId(record.id);
    setVehicleFormOpen(true);
    setErrors([]);
  }

  function closeVehicleForm() {
    setVehicle(initialVehicle);
    setEditingVehicleId(null);
    setVehicleFormOpen(false);
  }

  function openNewDriver() {
    setDriver(initialDriver);
    setEditingDriverId(null);
    setDriverFormOpen(true);
    setErrors([]);
  }

  function openDriver(record: Driver) {
    setDriver(driverFormFromRecord(record));
    setEditingDriverId(record.id);
    setDriverFormOpen(true);
    setErrors([]);
  }

  function closeDriverForm() {
    setDriver(initialDriver);
    setEditingDriverId(null);
    setDriverFormOpen(false);
  }

  async function saveVehicle() {
    const nextErrors: string[] = [];
    if (!vehicle.makeModel) nextErrors.push("Укажите марку и модель автобуса");
    if (!vehicle.registrationPlate) nextErrors.push("Укажите госномер автобуса");
    if (!vehicle.seats || Number(vehicle.seats) < 1) {
      nextErrors.push("Укажите корректное количество мест");
    }
    if (!vehicle.diagnosticCardNumber) {
      nextErrors.push("Укажите номер диагностической карты");
    }
    if (!vehicle.diagnosticCardValidUntil) {
      nextErrors.push("Укажите срок действия диагностической карты");
    }
    if (nextErrors.length > 0) {
      setErrors(nextErrors);
      return;
    }
    const payload: VehicleInput = {
      makeModel: vehicle.makeModel,
      registrationPlate: vehicle.registrationPlate,
      year: vehicle.year ? Number(vehicle.year) : null,
      seats: Number(vehicle.seats),
      diagnosticCardNumber: vehicle.diagnosticCardNumber,
      diagnosticCardValidUntil: vehicle.diagnosticCardValidUntil,
      notes: vehicle.notes,
      isActive: true,
    };
    setFleetSaving("vehicle");
    setErrors([]);
    try {
      const stored = editingVehicleId
        ? await api.updateVehicle(editingVehicleId, payload)
        : await api.createVehicle(payload);
      setVehicleEntries((current) =>
        editingVehicleId
          ? current.map((item) => (item.id === stored.id ? stored : item))
          : [...current, stored],
      );
      closeVehicleForm();
    } catch (error) {
      showRequestError(error);
    } finally {
      setFleetSaving(null);
    }
  }

  async function saveDriver() {
    const nextErrors: string[] = [];
    if (!driver.fullName) nextErrors.push("Укажите ФИО водителя");
    if (!driver.licenseNumber) {
      nextErrors.push("Укажите номер водительского удостоверения");
    }
    if (!driver.birthDate) nextErrors.push("Укажите дату рождения водителя");
    if (!driver.licenseIssuedAt) {
      nextErrors.push("Укажите дату выдачи водительского удостоверения");
    }
    if (nextErrors.length > 0) {
      setErrors(nextErrors);
      return;
    }
    const payload: DriverInput = {
      fullName: driver.fullName,
      birthDate: driver.birthDate,
      phone: driver.phone,
      licenseNumber: driver.licenseNumber,
      licenseCategories: driver.licenseCategories || "D",
      licenseIssuedAt: driver.licenseIssuedAt,
      experienceFrom: driver.experienceFrom,
      notes: driver.notes,
      isActive: true,
    };
    setFleetSaving("driver");
    setErrors([]);
    try {
      const stored = editingDriverId
        ? await api.updateDriver(editingDriverId, payload)
        : await api.createDriver(payload);
      setDriverEntries((current) =>
        editingDriverId
          ? current.map((item) => (item.id === stored.id ? stored : item))
          : [...current, stored],
      );
      closeDriverForm();
    } catch (error) {
      showRequestError(error);
    } finally {
      setFleetSaving(null);
    }
  }

  async function deleteVehicle(record: Vehicle) {
    if (!window.confirm(`Убрать автобус ${record.registrationPlate} из профиля?`)) return;
    setDeletingId(record.id);
    setErrors([]);
    try {
      await api.deleteVehicle(record.id);
      setVehicleEntries((current) => current.filter((item) => item.id !== record.id));
      if (editingVehicleId === record.id) closeVehicleForm();
    } catch (error) {
      showRequestError(error);
    } finally {
      setDeletingId(null);
    }
  }

  async function deleteDriver(record: Driver) {
    if (!window.confirm(`Убрать водителя ${record.fullName} из профиля?`)) return;
    setDeletingId(record.id);
    setErrors([]);
    try {
      await api.deleteDriver(record.id);
      setDriverEntries((current) => current.filter((item) => item.id !== record.id));
      if (editingDriverId === record.id) closeDriverForm();
    } catch (error) {
      showRequestError(error);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleContinue(event: FormEvent) {
    event.preventDefault();
    if (!validateCurrent()) return;
    setSaving(true);
    setErrors([]);

    try {
      if (activeStep.id === "company") {
        const carrier = await api.patchCarrier({
          legalName: profile.legalName,
          inn: profile.inn,
          legalAddress: profile.legalAddress,
          email: profile.email,
          phone: profile.phone,
        });
        const hydrated = profileFromCarrier(carrier);
        setProfile(hydrated);
        setCompanySaved(hasCompanyData(hydrated));
        setSignerSaved(hasSignerData(hydrated));
        setOrganizationName(hydrated.legalName || organizationName);
      } else if (activeStep.id === "signer") {
        const carrier = await api.patchCarrier({
          directorFullName: profile.signerName,
          directorPosition: profile.signerPosition,
        });
        const hydrated = profileFromCarrier(carrier);
        setProfile(hydrated);
        setCompanySaved(hasCompanyData(hydrated));
        setSignerSaved(hasSignerData(hydrated));
      } else if (activeStep.id === "assets") {
        if (signature) {
          await api.uploadAsset("signature", signature);
          setSignatureStored(true);
          setSignature(null);
        }
        if (stamp) {
          await api.uploadAsset("stamp", stamp);
          setStampStored(true);
          setUsesStamp(true);
          setStamp(null);
        }
      } else if (activeStep.id === "review") {
        const status = await api.completeOnboarding();
        if (!status.completed) {
          setErrors(status.missingFields);
          return;
        }
        router.replace("/");
        return;
      }
    } catch (error) {
      showRequestError(error);
      return;
    } finally {
      setSaving(false);
    }

    if (activeIndex < steps.length - 1) goTo(activeIndex + 1);
  }

  if (authState !== "ready") {
    return (
      <main className="onboarding-page">
        <div className="onboarding-auth-state">
          {authState === "checking" ? (
            <>
              <span className="spinner" />
              <strong>Проверяем доступ…</strong>
            </>
          ) : (
            <>
              <Icon name="alert" size={24} />
              <strong>Не удалось подключиться к API</strong>
              <p>{loadError}</p>
              <button
                className="button button-dark"
                onClick={() => window.location.reload()}
                type="button"
              >
                Повторить
              </button>
            </>
          )}
        </div>
      </main>
    );
  }

  return (
    <main className="onboarding-page">
      <header className="onboarding-topbar">
        <Link className="brand" href="/">
          <span className="brand-mark">B</span>
          <span>Buscript</span>
        </Link>
        <div className="onboarding-top-status">
          <span>{organizationName || "Профиль компании"}</span>
          <span className="top-divider" />
          <span>Первичная настройка</span>
        </div>
        <Link className="button button-secondary button-small" href="/">
          На главную
        </Link>
      </header>

      <div className="onboarding-layout">
        <aside className="onboarding-steps">
          <div className="onboarding-intro">
            <span className="eyebrow">Настройка профиля</span>
            <h1>Подготовим компанию к работе</h1>
            <p>Данные сохраняются поэтапно. До завершения профиля отправка уведомлений заблокирована.</p>
          </div>

          <div className="mobile-step-progress">
            <div>
              <span>
                Шаг {activeIndex + 1} из {steps.length}
              </span>
              <strong>{activeStep.short}</strong>
            </div>
            <ProgressBar value={progress} size="compact" />
          </div>

          <nav aria-label="Этапы настройки" className="step-list">
            {steps.map((step, index) => {
              const done = stepCompletion[step.id];
              const active = index === activeIndex;
              const missing =
                !done &&
                !active &&
                (visitedSteps.has(step.id) || index < activeIndex);
              return (
                <button
                  className={`${active ? "is-active" : ""} ${done ? "is-done" : ""} ${
                    missing ? "is-missing" : ""
                  }`}
                  key={step.id}
                  onClick={() => goTo(index)}
                  type="button"
                >
                  <span className="step-index">
                    {done ? <Icon name="check" size={14} /> : String(index + 1).padStart(2, "0")}
                  </span>
                  <span>
                    <strong>{step.short}</strong>
                    <small>
                      {missing ? `Не заполнено · ${step.description}` : step.description}
                    </small>
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="onboarding-security">
            <Icon name="shield" size={19} />
            <span>
              <strong>Изолированный профиль</strong>
              <small>Данные доступны только вашей компании.</small>
            </span>
          </div>
        </aside>

        <section className="onboarding-stage">
          <form className="onboarding-form" onSubmit={handleContinue}>
            <header className="step-header">
              <div className="step-header-icon">
                <Icon name={activeStep.icon} size={24} />
              </div>
              <div>
                <span className="eyebrow">
                  Шаг {activeIndex + 1} из {steps.length}
                </span>
                <h2>{activeStep.title}</h2>
                <p>{activeStep.description}</p>
              </div>
            </header>

            {activeStep.id === "company" && (
              <div className="form-section animate-in" key="company">
                <div className="section-heading">
                  <h3>Основные сведения</h3>
                  <p>Эти данные будут подставляться во все уведомления как данные перевозчика.</p>
                </div>
                <div className="field-grid">
                  <OnboardingField label="Полное наименование" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("legalName")}
                        placeholder="ООО «Название компании»"
                        value={profile.legalName}
                      />
                    </span>
                  </OnboardingField>
                  <OnboardingField label="ИНН" required>
                    <span className="input-shell">
                      <input
                        inputMode="numeric"
                        maxLength={12}
                        onChange={setField("inn")}
                        placeholder="0000000000"
                        value={profile.inn}
                      />
                    </span>
                  </OnboardingField>
                  <OnboardingField label="Юридический адрес" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("legalAddress")}
                        placeholder="г. Санкт-Петербург, …"
                        value={profile.legalAddress}
                      />
                    </span>
                  </OnboardingField>
                  <OnboardingField label="Электронная почта" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("email")}
                        placeholder="office@example.test"
                        type="email"
                        value={profile.email}
                      />
                    </span>
                  </OnboardingField>
                  <OnboardingField label="Телефон" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("phone")}
                        placeholder="+7 000 000-00-00"
                        type="tel"
                        value={profile.phone}
                      />
                    </span>
                  </OnboardingField>
                </div>
                <div className="calm-notice">
                  <Icon name="alert" size={18} />
                  <p>
                    Реквизиты перевозчика из заявок клиентов будут игнорироваться. Buscript всегда
                    использует подтверждённый профиль этой компании.
                  </p>
                </div>
              </div>
            )}

            {activeStep.id === "signer" && (
              <div className="form-section animate-in" key="signer">
                <div className="section-heading">
                  <h3>Ответственное лицо</h3>
                  <p>Укажите человека, чья подпись используется в уведомлениях.</p>
                </div>
                <div className="field-grid">
                  <OnboardingField label="ФИО полностью" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("signerName")}
                        placeholder="Иванов Иван Иванович"
                        value={profile.signerName}
                      />
                    </span>
                  </OnboardingField>
                  <OnboardingField label="Должность" required>
                    <span className="input-shell">
                      <input
                        onChange={setField("signerPosition")}
                        placeholder="Генеральный директор"
                        value={profile.signerPosition}
                      />
                    </span>
                  </OnboardingField>
                </div>
                <div className="signer-preview">
                  <span className="preview-monogram">
                    {profile.signerName
                      ? profile.signerName
                          .split(" ")
                          .slice(0, 2)
                          .map((part) => part[0])
                          .join("")
                      : "—"}
                  </span>
                  <span>
                    <small>Будет отображаться в уведомлении</small>
                    <strong>{profile.signerName || "ФИО подписанта"}</strong>
                    <p>{profile.signerPosition}</p>
                  </span>
                </div>
              </div>
            )}

            {activeStep.id === "assets" && (
              <div className="form-section animate-in" key="assets">
                <div className="section-heading">
                  <h3>Графические материалы</h3>
                  <p>Используйте PNG с прозрачным фоном. Buscript проверит границы и качество.</p>
                </div>
                <div className="asset-list">
                  <FileDrop
                    accept=".png,image/png"
                    description="PNG · прозрачный фон · до 10 МБ"
                    file={signature}
                    label="Подпись руководителя"
                    onFile={setSignature}
                    stored={signatureStored}
                  />
                  {usesStamp && (
                    <div className="optional-asset">
                      <div className="stamp-control-line">
                        <span>
                          <strong>Печать организации</strong>
                          <small>
                            {stampStored
                              ? "Печать сохранена в профиле"
                              : "Необязательный материал"}
                          </small>
                        </span>
                        {!stampStored && (
                          <button
                            className="button button-secondary button-small"
                            onClick={() => {
                              setStamp(null);
                              setUsesStamp(false);
                            }}
                            type="button"
                          >
                            Не добавлять
                          </button>
                        )}
                      </div>
                      <FileDrop
                        accept=".png,image/png"
                        description="PNG · прозрачный фон · до 10 МБ"
                        file={stamp}
                        label="Печать организации"
                        onFile={setStamp}
                        stored={stampStored}
                      />
                    </div>
                  )}
                  {!usesStamp && (
                    <button
                      className="optional-asset-trigger"
                      onClick={() => setUsesStamp(true)}
                      type="button"
                    >
                      <span><Icon name="plus" size={16} /></span>
                      <span>
                        <strong>Добавить печать</strong>
                        <small>Необязательно. Подписи достаточно для завершения профиля.</small>
                      </span>
                    </button>
                  )}
                </div>
                <div className="asset-guidelines">
                  <span><Icon name="check" size={15} /> Без белого фона</span>
                  <span><Icon name="check" size={15} /> Чёткие края</span>
                  <span><Icon name="check" size={15} /> Только PNG</span>
                </div>
              </div>
            )}

            {activeStep.id === "fleet" && (
              <div className="form-section animate-in" key="fleet">
                <div className="section-heading">
                  <h3>Транспорт и водители</h3>
                  <p>
                    Здесь показаны реальные записи из профиля. Новая запись появится только после
                    отдельного сохранения.
                  </p>
                </div>
                <div className="fleet-columns">
                  <div className="fleet-column">
                    <header>
                      <span><Icon name="bus" size={19} /> Автобусы</span>
                      <strong>{vehicleEntries.length}</strong>
                    </header>

                    {vehicleEntries.length > 0 && (
                      <div className="fleet-records">
                        {vehicleEntries.map((record) => (
                          <div className="fleet-added" key={record.id}>
                            <span className="fleet-added-icon">
                              <Icon name="bus" size={17} />
                            </span>
                            <span>
                              <strong>{record.makeModel}</strong>
                              <small>
                                {record.registrationPlate} · {record.seats} мест
                              </small>
                            </span>
                            <span className="fleet-record-actions">
                              <button
                                aria-label={`Изменить автобус ${record.registrationPlate}`}
                                disabled={deletingId === record.id}
                                onClick={() => openVehicle(record)}
                                title="Изменить"
                                type="button"
                              >
                                <Icon name="settings" size={13} />
                              </button>
                              <button
                                aria-label={`Удалить автобус ${record.registrationPlate}`}
                                disabled={deletingId === record.id}
                                onClick={() => void deleteVehicle(record)}
                                title="Удалить"
                                type="button"
                              >
                                {deletingId === record.id ? (
                                  <span className="spinner" />
                                ) : (
                                  <Icon name="close" size={13} />
                                )}
                              </button>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {!vehicleFormOpen ? (
                      <button
                        className={`fleet-empty ${
                          vehicleEntries.length > 0 ? "is-compact" : ""
                        }`}
                        onClick={openNewVehicle}
                        type="button"
                      >
                        <span><Icon name="plus" size={19} /></span>
                        <strong>Добавить автобус</strong>
                        <small>Госномер, марка, места и диагностическая карта</small>
                      </button>
                    ) : (
                      <div className="fleet-entry-form">
                        <div className="fleet-entry-title">
                          <span>
                            <Icon name="bus" size={16} />
                            {editingVehicleId ? "Изменение автобуса" : "Новый автобус"}
                          </span>
                          <button
                            aria-label="Закрыть форму автобуса"
                            onClick={closeVehicleForm}
                            type="button"
                          >
                            <Icon name="close" size={14} />
                          </button>
                        </div>
                        <label>
                          <span>Марка и модель *</span>
                          <input
                            onChange={(event) =>
                              setVehicle((value) => ({
                                ...value,
                                makeModel: event.target.value,
                              }))
                            }
                            placeholder="Mercedes-Benz Sprinter"
                            value={vehicle.makeModel}
                          />
                        </label>
                        <label>
                          <span>Госномер *</span>
                          <input
                            onChange={(event) =>
                              setVehicle((value) => ({
                                ...value,
                                registrationPlate: event.target.value,
                              }))
                            }
                            placeholder="А000АА178"
                            value={vehicle.registrationPlate}
                          />
                        </label>
                        <div className="fleet-inline-fields">
                          <label>
                            <span>Год выпуска</span>
                            <input
                              inputMode="numeric"
                              onChange={(event) =>
                                setVehicle((value) => ({
                                  ...value,
                                  year: event.target.value,
                                }))
                              }
                              placeholder="2022"
                              value={vehicle.year}
                            />
                          </label>
                          <label>
                            <span>Мест *</span>
                            <input
                              inputMode="numeric"
                              onChange={(event) =>
                                setVehicle((value) => ({
                                  ...value,
                                  seats: event.target.value,
                                }))
                              }
                              value={vehicle.seats}
                            />
                          </label>
                        </div>
                        <label>
                          <span>Диагностическая карта *</span>
                          <input
                            onChange={(event) =>
                              setVehicle((value) => ({
                                ...value,
                                diagnosticCardNumber: event.target.value,
                              }))
                            }
                            placeholder="Номер карты"
                            value={vehicle.diagnosticCardNumber}
                          />
                        </label>
                        <label>
                          <span>Действует до *</span>
                          <input
                            onChange={(event) =>
                              setVehicle((value) => ({
                                ...value,
                                diagnosticCardValidUntil: event.target.value,
                              }))
                            }
                            type="date"
                            value={vehicle.diagnosticCardValidUntil}
                          />
                        </label>
                        <div className="fleet-entry-actions">
                          <button
                            className="button button-secondary button-small"
                            onClick={closeVehicleForm}
                            type="button"
                          >
                            Отмена
                          </button>
                          <button
                            className="button button-dark button-small"
                            disabled={fleetSaving === "vehicle"}
                            onClick={() => void saveVehicle()}
                            type="button"
                          >
                            {fleetSaving === "vehicle" ? "Сохраняем…" : "Сохранить автобус"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="fleet-column">
                    <header>
                      <span><Icon name="profile" size={19} /> Водители</span>
                      <strong>{driverEntries.length}</strong>
                    </header>

                    {driverEntries.length > 0 && (
                      <div className="fleet-records">
                        {driverEntries.map((record) => (
                          <div className="fleet-added" key={record.id}>
                            <span className="fleet-added-icon">
                              <Icon name="profile" size={17} />
                            </span>
                            <span>
                              <strong>{record.fullName}</strong>
                              <small>
                                Права {record.licenseNumber} · {record.licenseCategories}
                              </small>
                            </span>
                            <span className="fleet-record-actions">
                              <button
                                aria-label={`Изменить водителя ${record.fullName}`}
                                disabled={deletingId === record.id}
                                onClick={() => openDriver(record)}
                                title="Изменить"
                                type="button"
                              >
                                <Icon name="settings" size={13} />
                              </button>
                              <button
                                aria-label={`Удалить водителя ${record.fullName}`}
                                disabled={deletingId === record.id}
                                onClick={() => void deleteDriver(record)}
                                title="Удалить"
                                type="button"
                              >
                                {deletingId === record.id ? (
                                  <span className="spinner" />
                                ) : (
                                  <Icon name="close" size={13} />
                                )}
                              </button>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}

                    {!driverFormOpen ? (
                      <button
                        className={`fleet-empty ${
                          driverEntries.length > 0 ? "is-compact" : ""
                        }`}
                        onClick={openNewDriver}
                        type="button"
                      >
                        <span><Icon name="plus" size={19} /></span>
                        <strong>Добавить водителя</strong>
                        <small>ФИО, телефон, права и стаж</small>
                      </button>
                    ) : (
                      <div className="fleet-entry-form">
                        <div className="fleet-entry-title">
                          <span>
                            <Icon name="profile" size={16} />
                            {editingDriverId ? "Изменение водителя" : "Новый водитель"}
                          </span>
                          <button
                            aria-label="Закрыть форму водителя"
                            onClick={closeDriverForm}
                            type="button"
                          >
                            <Icon name="close" size={14} />
                          </button>
                        </div>
                        <label>
                          <span>ФИО *</span>
                          <input
                            onChange={(event) =>
                              setDriver((value) => ({
                                ...value,
                                fullName: event.target.value,
                              }))
                            }
                            placeholder="Иванов Иван Иванович"
                            value={driver.fullName}
                          />
                        </label>
                        <label>
                          <span>Телефон</span>
                          <input
                            onChange={(event) =>
                              setDriver((value) => ({
                                ...value,
                                phone: event.target.value,
                              }))
                            }
                            placeholder="+7 000 000-00-00"
                            value={driver.phone}
                          />
                        </label>
                        <label>
                          <span>Дата рождения *</span>
                          <input
                            onChange={(event) =>
                              setDriver((value) => ({
                                ...value,
                                birthDate: event.target.value,
                              }))
                            }
                            type="date"
                            value={driver.birthDate}
                          />
                        </label>
                        <div className="fleet-inline-fields">
                          <label>
                            <span>Удостоверение *</span>
                            <input
                              onChange={(event) =>
                                setDriver((value) => ({
                                  ...value,
                                  licenseNumber: event.target.value,
                                }))
                              }
                              placeholder="00 00 000000"
                              value={driver.licenseNumber}
                            />
                          </label>
                          <label>
                            <span>Категория</span>
                            <input
                              onChange={(event) =>
                                setDriver((value) => ({
                                  ...value,
                                  licenseCategories: event.target.value,
                                }))
                              }
                              value={driver.licenseCategories}
                            />
                          </label>
                        </div>
                        <div className="fleet-inline-fields">
                          <label>
                            <span>Права выданы *</span>
                            <input
                              onChange={(event) =>
                                setDriver((value) => ({
                                  ...value,
                                  licenseIssuedAt: event.target.value,
                                }))
                              }
                              type="date"
                              value={driver.licenseIssuedAt}
                            />
                          </label>
                          <label>
                            <span>Стаж с</span>
                            <input
                              onChange={(event) =>
                                setDriver((value) => ({
                                  ...value,
                                  experienceFrom: event.target.value,
                                }))
                              }
                              type="date"
                              value={driver.experienceFrom}
                            />
                          </label>
                        </div>
                        <div className="fleet-entry-actions">
                          <button
                            className="button button-secondary button-small"
                            onClick={closeDriverForm}
                            type="button"
                          >
                            Отмена
                          </button>
                          <button
                            className="button button-dark button-small"
                            disabled={fleetSaving === "driver"}
                            onClick={() => void saveDriver()}
                            type="button"
                          >
                            {fleetSaving === "driver" ? "Сохраняем…" : "Сохранить водителя"}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {activeStep.id === "review" && (
              <div className="form-section animate-in" key="review">
                <div className="review-hero">
                  <span className={`review-mark ${completion.ready ? "is-ready" : ""}`}>
                    <Icon name={completion.ready ? "check" : "alert"} size={28} />
                  </span>
                  <div>
                    <span className="eyebrow">Проверка профиля</span>
                    <h3>
                      {completion.ready
                        ? "Профиль готов к подтверждению"
                        : `Заполнено ${completion.count} из ${completion.total} разделов`}
                    </h3>
                    <p>
                      {completion.ready
                        ? "Все обязательные данные сохранены в базе. Подтвердите готовность профиля."
                        : "Вернитесь к незаполненным разделам. Черновики доступны уже сейчас, подача — после подтверждения."}
                    </p>
                  </div>
                </div>
                <div className="review-list">
                  {steps.slice(0, -1).map((step, index) => {
                    const states = [
                      companySaved,
                      signerSaved,
                      signatureStored,
                      vehicleEntries.length > 0 && driverEntries.length > 0,
                    ];
                    return (
                      <button
                        className={states[index] ? "is-complete" : "is-missing"}
                        onClick={() => goTo(index)}
                        type="button"
                        key={step.id}
                      >
                        <span className={states[index] ? "is-complete" : ""}>
                          <Icon name={states[index] ? "check" : step.icon} size={17} />
                        </span>
                        <span>
                          <strong>{step.short}</strong>
                          <small>{states[index] ? "Заполнено" : "Не заполнено"}</small>
                        </span>
                        <Icon name="chevron" size={17} />
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {errors.length > 0 && (
              <div className="form-errors" role="alert">
                <Icon name="alert" size={18} />
                <div>
                  <strong>Проверьте обязательные данные</strong>
                  {errors.map((error) => <span key={error}>{error}</span>)}
                </div>
              </div>
            )}

            <footer className="onboarding-actions">
              <button
                className="button button-secondary"
                disabled={activeIndex === 0}
                onClick={() => goTo(activeIndex - 1)}
                type="button"
              >
                <Icon name="back" size={16} />
                Назад
              </button>
              {activeStep.id === "review" ? (
                <button
                  className="button button-dark"
                  disabled={!completion.ready || saving}
                  type="submit"
                >
                  Подтвердить профиль
                  <span className="button-icon-circle"><Icon name="check" size={15} /></span>
                </button>
              ) : (
                <button className="button button-dark" disabled={saving} type="submit">
                  {saving ? "Сохраняем…" : "Сохранить и продолжить"}
                  <span className="button-icon-circle">
                    {saving ? <span className="spinner" /> : <Icon name="arrow" size={15} />}
                  </span>
                </button>
              )}
            </footer>
          </form>
        </section>
      </div>
    </main>
  );
}
