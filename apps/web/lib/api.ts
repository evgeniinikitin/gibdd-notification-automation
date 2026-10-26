import type {
  AdminTenant,
  AuthTokens,
  BalanceSummary,
  BalanceTransaction,
  CarrierProfile,
  ChatMessage,
  Conversation,
  ConversationWorkflow,
  Driver,
  DriverInput,
  FileEntry,
  MissingClarification,
  OnboardingStatus,
  OnboardingStepId,
  ProfileAsset,
  RegistrationInput,
  SubmissionAttempt,
  Trip,
  TripAutomation,
  TripStatus,
  User,
  Vehicle,
  VehicleInput,
  WorkflowRun,
  WorkflowTrip,
} from "@/lib/types";

// Use the current Buscript origin by default. In Docker, Next.js proxies
// /api/v1 to the API container, so a host VPN never sees port 8000.
const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
const API_PREFIX = "/api/v1";
const ACCESS_TOKEN_KEY = "buscript.access_token";
const REFRESH_TOKEN_KEY = "buscript.refresh_token";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function getStoredToken(key: string) {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(key) ?? window.sessionStorage.getItem(key);
}

function getAccessToken() {
  return getStoredToken(ACCESS_TOKEN_KEY);
}

function usesPersistentStorage() {
  return typeof window !== "undefined" && Boolean(window.localStorage.getItem(REFRESH_TOKEN_KEY));
}

export function saveTokens(tokens: AuthTokens, persistent = true) {
  if (typeof window === "undefined") return;
  clearTokens();
  const storage = persistent ? window.localStorage : window.sessionStorage;
  storage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken);
  if (tokens.refreshToken) storage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken);
}

export function clearTokens() {
  if (typeof window === "undefined") return;
  for (const storage of [window.localStorage, window.sessionStorage]) {
    storage.removeItem(ACCESS_TOKEN_KEY);
    storage.removeItem(REFRESH_TOKEN_KEY);
  }
}

type TokenPairDto = {
  access_token: string;
  refresh_token: string;
};

let refreshPromise: Promise<boolean> | null = null;

async function performSessionRefresh() {
  const refreshToken = getStoredToken(REFRESH_TOKEN_KEY);
  if (!refreshToken) return false;
  let response: Response;
  try {
    response = await fetch(`${API_URL}${API_PREFIX}/auth/refresh`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
  } catch {
    return false;
  }
  if (!response.ok) {
    clearTokens();
    return false;
  }
  const dto = (await response.json()) as TokenPairDto;
  saveTokens(
    { accessToken: dto.access_token, refreshToken: dto.refresh_token },
    usesPersistentStorage(),
  );
  return true;
}

function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = performSessionRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function fetchResponse(
  path: string,
  init: RequestInit = {},
  retryAuth = true,
  retryNetwork = true,
) {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("Accept", "application/json");
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(`${API_URL}${API_PREFIX}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch {
    const method = (init.method ?? "GET").toUpperCase();
    if (retryNetwork && method === "GET") {
      return fetchResponse(path, init, retryAuth, false);
    }
    throw new ApiError("Не удалось подключиться к серверу. Проверьте, что API запущен.");
  }

  if (response.status === 401 && retryAuth && (await refreshSession())) {
    return fetchResponse(path, init, false, retryNetwork);
  }
  return response;
}

async function responseError(response: Response) {
  let detail = `Ошибка API: ${response.status}`;
  try {
    const body = (await response.json()) as {
      detail?:
        | string
        | Array<{ msg?: string }>
        | { message?: string; missing_fields?: string[] };
    };
    if (typeof body.detail === "string") detail = body.detail;
    if (Array.isArray(body.detail)) {
      detail = body.detail.map((item) => item.msg).filter(Boolean).join(". ") || detail;
    }
    if (
      typeof body.detail === "object" &&
      body.detail !== null &&
      !Array.isArray(body.detail)
    ) {
      const message = body.detail.message?.trim();
      const missing = body.detail.missing_fields?.filter(Boolean) ?? [];
      const combined = [message, missing.length > 0 ? missing.join(", ") : null]
        .filter(Boolean)
        .join(": ");
      if (combined) detail = combined;
    }
  } catch {
    // The status-based message remains readable for non-JSON errors.
  }
  return new ApiError(detail, response.status);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetchResponse(path, init);
  if (!response.ok) throw await responseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

type UserDto = {
  id: string;
  tenant_id: string;
  email: string;
  full_name: string;
  role: User["role"];
  tenant?: { id: string; name: string } | null;
};

type OnboardingDto = {
  completed: boolean;
  completion_percent: number;
  requirements: Array<{
    key: string;
    label: string;
    completed: boolean;
    path: string;
  }>;
};

type CarrierProfileDto = {
  id?: string;
  legal_name: string | null;
  inn: string | null;
  legal_address: string | null;
  actual_address: string | null;
  phone: string | null;
  email: string | null;
  director_position: string | null;
  director_full_name: string | null;
  license_number: string | null;
  license_issued_at: string | null;
  updated_at?: string;
};

type ProfileAssetDto = {
  id: string;
  file_id: string;
  kind: "signature" | "stamp";
  created_at: string;
};

type VehicleDto = {
  id: string;
  make_model: string;
  registration_plate: string;
  year: number | null;
  seats: number;
  diagnostic_card_number: string | null;
  diagnostic_card_valid_until: string | null;
  notes: string | null;
  is_active: boolean;
  updated_at: string;
};

type DriverDto = {
  id: string;
  full_name: string;
  birth_date: string | null;
  phone: string | null;
  license_number: string;
  license_categories: string;
  license_issued_at: string | null;
  experience_from: string | null;
  notes: string | null;
  is_active: boolean;
  updated_at: string;
};

type ConversationDto = {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
};

type MessageDto = {
  id: string;
  role: string;
  content: string;
  attachments: string[];
  metadata_json?: Record<string, unknown>;
  created_at: string;
};

type ConversationDetailDto = ConversationDto & {
  messages: MessageDto[];
};

type TripDto = {
  id: string;
  reference: string;
  conversation_id?: string | null;
  title: string;
  status: string;
  progress: number;
  trip_date: string | null;
  start_time: string | null;
  end_time?: string | null;
  route_text: string | null;
  customer_type?: string | null;
  customer_name: string | null;
  customer_inn?: string | null;
  customer_address?: string | null;
  passenger_count: number | null;
  selected_vehicle_id?: string | null;
  selected_driver_id?: string | null;
  extracted_data?: Record<string, unknown>;
  failure_reason?: string | null;
  confirmed_at?: string | null;
  portal_token?: string | null;
  submitted_at?: string | null;
  updated_at: string;
};

type WorkflowRunDto = {
  id: string;
  status: string;
  revision: number;
  provider?: string | null;
  model?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

type MissingClarificationDto = {
  field: string;
  label: string;
  question: string;
  suggested_value?: string | null;
  reason?: string | null;
};

type ConversationWorkflowDto = {
  trip: TripDto;
  run?: WorkflowRunDto | null;
  extracted_fields?: Record<string, unknown>;
  missing_clarifications?: MissingClarificationDto[];
  uncertain_fields?: Array<
    MissingClarificationDto | string | Record<string, unknown>
  >;
  summary?: string | null;
  can_confirm?: boolean;
  configuration_required?: boolean;
};

type SubmissionAttemptDto = {
  id: string;
  attempt_number: number;
  status: string;
  final_submit_attempted: boolean;
  current_url?: string | null;
  diagnostic_file_ids: string[];
  error_message?: string | null;
  started_at: string;
  completed_at?: string | null;
};

type TripAutomationDto = {
  trip_id: string;
  job_id?: string | null;
  status: string;
  payload_id?: string | null;
  notification_docx_file_id?: string | null;
  notification_pdf_file_id?: string | null;
  live_submission: boolean;
  attempt_count: number;
  final_submit_attempted: boolean;
  portal_url?: string | null;
  portal_token?: string | null;
  last_error?: string | null;
  attempts: SubmissionAttemptDto[];
};

type FileDto = {
  id: string;
  original_name: string;
  kind: string;
  content_type?: string;
  size: number;
  created_at: string;
  trip_id: string | null;
  trip_reference: string | null;
};

type BalanceDto = {
  available: number | string;
  reserved: number | string;
  total: number | string;
};

type TransactionDto = {
  id: string;
  kind: string;
  amount: number | string;
  note: string | null;
  trip_id: string | null;
  trip_reference: string | null;
  actor_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  created_at: string;
};

type AdminTenantDto = {
  id?: string;
  tenant_id?: string;
  name?: string;
  company_name?: string;
  primary_admin_email?: string | null;
  primary_admin_full_name?: string | null;
  is_active?: boolean;
  profile_completed?: boolean;
  onboarding_completed?: boolean;
  onboarding_completion_percent?: number;
  trips_count?: number;
  trip_count?: number;
  submitted_trips_count?: number;
  submitted_trip_count?: number;
  available?: number | string;
  available_notifications?: number | string;
  reserved?: number | string;
  reserved_notifications?: number | string;
  balance?: {
    available?: number | string;
    reserved?: number | string;
  };
  created_at?: string;
};

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ru", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function mapUser(dto: UserDto): User {
  return {
    id: dto.id,
    email: dto.email,
    fullName: dto.full_name,
    role: dto.role,
    organizationId: dto.tenant_id,
    organizationName:
      dto.role === "admin" ? "Buscript" : (dto.tenant?.name ?? "Без названия"),
  };
}

function mapOnboarding(dto: OnboardingDto): OnboardingStatus {
  const byKey = new Map(dto.requirements.map((item) => [item.key, item.completed]));
  const completedSteps: OnboardingStepId[] = [];
  if (byKey.get("carrier")) completedSteps.push("company", "signer");
  if (byKey.get("signature")) completedSteps.push("assets");
  if (byKey.get("vehicle") && byKey.get("driver")) completedSteps.push("fleet");
  if (dto.completed) completedSteps.push("review");
  const orderedSteps: OnboardingStepId[] = ["company", "signer", "assets", "fleet", "review"];
  return {
    completed: dto.completed,
    completionPercent: dto.completion_percent,
    currentStep: orderedSteps.find((step) => !completedSteps.includes(step)) ?? "review",
    completedSteps,
    missingFields: dto.requirements.filter((item) => !item.completed).map((item) => item.label),
  };
}

function mapCarrier(dto: CarrierProfileDto): CarrierProfile {
  return {
    id: dto.id,
    legalName: dto.legal_name ?? "",
    inn: dto.inn ?? "",
    legalAddress: dto.legal_address ?? "",
    actualAddress: dto.actual_address ?? "",
    phone: dto.phone ?? "",
    email: dto.email ?? "",
    directorPosition: dto.director_position ?? "",
    directorFullName: dto.director_full_name ?? "",
    licenseNumber: dto.license_number ?? "",
    licenseIssuedAt: dto.license_issued_at ?? "",
    updatedAt: dto.updated_at,
  };
}

function vehiclePayload(payload: VehicleInput | Record<string, unknown>) {
  return {
    make_model: payload.makeModel,
    registration_plate: payload.registrationPlate,
    year: payload.year ? Number(payload.year) : null,
    seats: Number(payload.seats),
    diagnostic_card_number: payload.diagnosticCardNumber || null,
    diagnostic_card_valid_until: payload.diagnosticCardValidUntil || null,
    notes: payload.notes || null,
    is_active: payload.isActive ?? true,
  };
}

function mapVehicle(dto: VehicleDto): Vehicle {
  return {
    id: dto.id,
    makeModel: dto.make_model,
    registrationPlate: dto.registration_plate,
    year: dto.year,
    seats: dto.seats,
    diagnosticCardNumber: dto.diagnostic_card_number ?? "",
    diagnosticCardValidUntil: dto.diagnostic_card_valid_until ?? "",
    notes: dto.notes ?? "",
    isActive: dto.is_active,
    updatedAt: dto.updated_at,
  };
}

function driverPayload(payload: DriverInput | Record<string, unknown>) {
  return {
    full_name: payload.fullName,
    birth_date: payload.birthDate || null,
    phone: payload.phone || null,
    license_number: payload.licenseNumber,
    license_categories: payload.licenseCategories || "D",
    license_issued_at: payload.licenseIssuedAt || null,
    experience_from: payload.experienceFrom || null,
    notes: payload.notes || null,
    is_active: payload.isActive ?? true,
  };
}

function mapDriver(dto: DriverDto): Driver {
  return {
    id: dto.id,
    fullName: dto.full_name,
    birthDate: dto.birth_date ?? "",
    phone: dto.phone ?? "",
    licenseNumber: dto.license_number,
    licenseCategories: dto.license_categories,
    licenseIssuedAt: dto.license_issued_at ?? "",
    experienceFrom: dto.experience_from ?? "",
    notes: dto.notes ?? "",
    isActive: dto.is_active,
    updatedAt: dto.updated_at,
  };
}

function mapConversation(dto: ConversationDto): Conversation {
  return {
    id: dto.id,
    title: dto.title,
    preview: dto.status === "archived" ? "Диалог в архиве" : "Открыть диалог",
    updatedAt: formatDateTime(dto.updated_at),
  };
}

function mapMessage(dto: MessageDto, files = new Map<string, FileDto>()): ChatMessage {
  const author = dto.role === "user" ? "user" : dto.role === "system" ? "system" : "assistant";
  const kind =
    typeof dto.metadata_json?.message_type === "string"
      ? dto.metadata_json.message_type
      : typeof dto.metadata_json?.type === "string"
      ? dto.metadata_json.type
      : typeof dto.metadata_json?.kind === "string"
        ? dto.metadata_json.kind
        : undefined;
  return {
    id: dto.id,
    author,
    body: dto.content || (dto.attachments.length ? "Вложение без комментария" : ""),
    createdAt: new Intl.DateTimeFormat("ru", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(dto.created_at)),
    kind,
    metadata: dto.metadata_json,
    attachments: dto.attachments
      .map((id) => files.get(id))
      .filter((item): item is FileDto => Boolean(item))
      .map((item) => ({
        id: item.id,
        name: item.original_name,
        type: item.content_type ?? item.kind,
        size: item.size,
        status: "ready" as const,
      })),
  };
}

function mapTripStatus(status: string): TripStatus {
  const statuses: Record<string, TripStatus> = {
    draft: "draft",
    extracting: "extracting",
    clarifying: "clarifying",
    ready: "validated",
    confirmed: "validated",
    generating: "generating_documents",
    generating_documents: "generating_documents",
    building_payload: "generating_documents",
    ready_to_submit: "generating_documents",
    awaiting_captcha: "clarifying",
    submitting: "generating_documents",
    dry_run_ready: "dry_run_ready",
    submitted: "submitted",
    completed: "completed",
    failed: "needs_support",
    needs_support: "needs_support",
    cancelled: "draft",
  };
  return statuses[status] ?? "draft";
}

function mapTrip(dto: TripDto): Trip {
  const tripDate = dto.trip_date
    ? new Intl.DateTimeFormat("ru", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(new Date(`${dto.trip_date}T00:00:00`))
    : "Дата не указана";
  return {
    id: dto.id,
    reference: dto.reference,
    customer: dto.customer_name ?? "Заказчик не определён",
    date: dto.start_time ? `${tripDate} · ${dto.start_time}` : tripDate,
    route: dto.route_text ?? "Маршрут уточняется",
    childrenCount: dto.passenger_count ?? 0,
    status: mapTripStatus(dto.status),
    progress: dto.progress,
    updatedAt: formatDateTime(dto.updated_at),
  };
}

function mapWorkflowTrip(dto: TripDto): WorkflowTrip {
  return {
    id: dto.id,
    reference: dto.reference,
    conversationId: dto.conversation_id ?? undefined,
    title: dto.title,
    status: dto.status,
    progress: dto.progress,
    tripDate: dto.trip_date ?? undefined,
    startTime: dto.start_time ?? undefined,
    endTime: dto.end_time ?? undefined,
    routeText: dto.route_text ?? undefined,
    customerType: dto.customer_type ?? undefined,
    customerName: dto.customer_name ?? undefined,
    customerInn: dto.customer_inn ?? undefined,
    customerAddress: dto.customer_address ?? undefined,
    passengerCount: dto.passenger_count ?? undefined,
    selectedVehicleId: dto.selected_vehicle_id ?? undefined,
    selectedDriverId: dto.selected_driver_id ?? undefined,
    extractedData: dto.extracted_data ?? {},
    failureReason: dto.failure_reason ?? undefined,
    confirmedAt: dto.confirmed_at ?? undefined,
    portalToken: dto.portal_token ?? undefined,
    submittedAt: dto.submitted_at ?? undefined,
    updatedAt: dto.updated_at,
  };
}

function mapSubmissionAttempt(dto: SubmissionAttemptDto): SubmissionAttempt {
  return {
    id: dto.id,
    attemptNumber: dto.attempt_number,
    status: dto.status,
    finalSubmitAttempted: dto.final_submit_attempted,
    currentUrl: dto.current_url ?? undefined,
    diagnosticFileIds: dto.diagnostic_file_ids,
    errorMessage: dto.error_message ?? undefined,
    startedAt: dto.started_at,
    completedAt: dto.completed_at ?? undefined,
  };
}

function mapTripAutomation(dto: TripAutomationDto): TripAutomation {
  return {
    tripId: dto.trip_id,
    jobId: dto.job_id ?? undefined,
    status: dto.status,
    payloadId: dto.payload_id ?? undefined,
    notificationDocxFileId: dto.notification_docx_file_id ?? undefined,
    notificationPdfFileId: dto.notification_pdf_file_id ?? undefined,
    liveSubmission: dto.live_submission,
    attemptCount: dto.attempt_count,
    finalSubmitAttempted: dto.final_submit_attempted,
    portalUrl: dto.portal_url ?? undefined,
    portalToken: dto.portal_token ?? undefined,
    lastError: dto.last_error ?? undefined,
    attempts: dto.attempts.map(mapSubmissionAttempt),
  };
}

function mapWorkflowRun(dto: WorkflowRunDto): WorkflowRun {
  return {
    id: dto.id,
    status: dto.status,
    revision: dto.revision,
    provider: dto.provider ?? undefined,
    model: dto.model ?? undefined,
    errorCode: dto.error_code ?? undefined,
    errorMessage: dto.error_message ?? undefined,
    createdAt: dto.created_at ?? undefined,
    startedAt: dto.started_at ?? undefined,
    finishedAt: dto.finished_at ?? undefined,
  };
}

function mapClarification(dto: MissingClarificationDto): MissingClarification {
  return {
    field: dto.field,
    label: dto.label,
    question: dto.question,
    suggestedValue: dto.suggested_value ?? undefined,
    reason: dto.reason ?? undefined,
  };
}

function isClarificationDto(
  value: MissingClarificationDto | string | Record<string, unknown>,
): value is MissingClarificationDto {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof value.field === "string" &&
    typeof value.label === "string" &&
    typeof value.question === "string"
  );
}

function mapConversationWorkflow(dto: ConversationWorkflowDto): ConversationWorkflow {
  return {
    trip: mapWorkflowTrip(dto.trip),
    run: dto.run ? mapWorkflowRun(dto.run) : undefined,
    extractedFields: dto.extracted_fields ?? {},
    missingClarifications: (dto.missing_clarifications ?? []).map(mapClarification),
    uncertainFields: (dto.uncertain_fields ?? []).map((item) =>
      isClarificationDto(item) ? mapClarification(item) : item,
    ),
    summary: dto.summary ?? undefined,
    canConfirm: dto.can_confirm ?? false,
    configurationRequired: dto.configuration_required ?? false,
  };
}

function mapFileKind(dto: FileDto): FileEntry["kind"] {
  const extension = dto.original_name.split(".").pop()?.toLowerCase();
  if (
    extension &&
    ["pdf", "docx", "xlsx", "png", "jpg", "jpeg", "html"].includes(extension)
  ) {
    return extension as FileEntry["kind"];
  }
  return "docx";
}

function mapFile(dto: FileDto): FileEntry {
  return {
    id: dto.id,
    name: dto.original_name,
    kind: mapFileKind(dto),
    artifactKind: dto.kind,
    tripId: dto.trip_id ?? undefined,
    size: dto.size,
    updatedAt: formatDateTime(dto.created_at),
    status: "ready",
    tripReference: dto.trip_reference ?? undefined,
  };
}

function mapAdminTenant(dto: AdminTenantDto): AdminTenant {
  return {
    id: dto.id ?? dto.tenant_id ?? "",
    name: dto.name ?? dto.company_name ?? "Без названия",
    isActive: dto.is_active ?? true,
    primaryAdminEmail: dto.primary_admin_email ?? undefined,
    primaryAdminFullName: dto.primary_admin_full_name ?? undefined,
    profileCompleted: dto.profile_completed ?? false,
    onboardingCompleted: dto.onboarding_completed ?? false,
    onboardingCompletionPercent: dto.onboarding_completion_percent ?? 0,
    tripsCount: dto.trip_count ?? dto.trips_count ?? 0,
    submittedTripsCount: dto.submitted_trip_count ?? dto.submitted_trips_count ?? 0,
    available: Number(
      dto.available_notifications ?? dto.available ?? dto.balance?.available ?? 0,
    ),
    reserved: Number(
      dto.reserved_notifications ?? dto.reserved ?? dto.balance?.reserved ?? 0,
    ),
    createdAt: dto.created_at,
  };
}

export const api = {
  async login(email: string, password: string): Promise<AuthTokens> {
    const dto = await request<TokenPairDto>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return { accessToken: dto.access_token, refreshToken: dto.refresh_token };
  },
  async register(payload: RegistrationInput): Promise<AuthTokens> {
    const dto = await request<TokenPairDto>("/auth/register", {
      method: "POST",
      body: JSON.stringify({
        company_name: payload.companyName,
        full_name: payload.fullName,
        email: payload.email,
        password: payload.password,
      }),
    });
    return { accessToken: dto.access_token, refreshToken: dto.refresh_token };
  },
  async logout() {
    try {
      await request<void>("/auth/logout", { method: "POST" });
    } finally {
      clearTokens();
    }
  },
  async me() {
    return mapUser(await request<UserDto>("/auth/me"));
  },
  async updateAccount(payload: { fullName: string; companyName: string }) {
    return mapUser(
      await request<UserDto>("/auth/me", {
        method: "PUT",
        body: JSON.stringify({
          full_name: payload.fullName,
          company_name: payload.companyName,
        }),
      }),
    );
  },
  async onboarding() {
    return mapOnboarding(await request<OnboardingDto>("/onboarding/status"));
  },
  async carrier() {
    const dto = await request<CarrierProfileDto | null>("/onboarding/carrier");
    return dto ? mapCarrier(dto) : null;
  },
  async updateCarrier(payload: Partial<CarrierProfile> & Record<string, unknown>) {
    const dto = await request<CarrierProfileDto>("/onboarding/carrier", {
      method: "PUT",
      body: JSON.stringify({
        legal_name: payload.legalName,
        inn: payload.inn,
        legal_address: payload.legalAddress,
        actual_address: payload.actualAddress || null,
        phone: payload.phone,
        email: payload.email,
        director_position: payload.directorPosition ?? payload.signerPosition,
        director_full_name: payload.directorFullName ?? payload.signerName,
        license_number: payload.licenseNumber || null,
        license_issued_at: payload.licenseIssuedAt || null,
      }),
    });
    return mapCarrier(dto);
  },
  async patchCarrier(payload: Partial<CarrierProfile> & Record<string, unknown>) {
    const fieldMap: Array<[string, string]> = [
      ["legalName", "legal_name"],
      ["inn", "inn"],
      ["legalAddress", "legal_address"],
      ["actualAddress", "actual_address"],
      ["phone", "phone"],
      ["email", "email"],
      ["directorPosition", "director_position"],
      ["directorFullName", "director_full_name"],
      ["signerPosition", "director_position"],
      ["signerName", "director_full_name"],
      ["licenseNumber", "license_number"],
      ["licenseIssuedAt", "license_issued_at"],
    ];
    const body: Record<string, unknown> = {};
    for (const [source, target] of fieldMap) {
      if (payload[source] !== undefined) body[target] = payload[source] || null;
    }
    return mapCarrier(
      await request<CarrierProfileDto>("/onboarding/carrier", {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    );
  },
  async assets() {
    const rows = await request<ProfileAssetDto[]>("/onboarding/assets");
    return rows.map(
      (item): ProfileAsset => ({
        id: item.id,
        fileId: item.file_id,
        kind: item.kind,
        createdAt: item.created_at,
      }),
    );
  },
  async uploadAsset(kind: "signature" | "stamp", file: File) {
    const data = new FormData();
    data.append("file", file);
    const item = await request<ProfileAssetDto>(`/onboarding/assets/${kind}`, {
      method: "POST",
      body: data,
    });
    return {
      id: item.id,
      fileId: item.file_id,
      kind: item.kind,
      createdAt: item.created_at,
    } satisfies ProfileAsset;
  },
  async vehicles() {
    return (await request<VehicleDto[]>("/vehicles")).map(mapVehicle);
  },
  async createVehicle(payload: VehicleInput | Record<string, unknown>) {
    return mapVehicle(
      await request<VehicleDto>("/vehicles", {
        method: "POST",
        body: JSON.stringify(vehiclePayload(payload)),
      }),
    );
  },
  async updateVehicle(id: string, payload: VehicleInput) {
    return mapVehicle(
      await request<VehicleDto>(`/vehicles/${id}`, {
        method: "PUT",
        body: JSON.stringify(vehiclePayload(payload)),
      }),
    );
  },
  deleteVehicle(id: string) {
    return request<void>(`/vehicles/${id}`, { method: "DELETE" });
  },
  async drivers() {
    return (await request<DriverDto[]>("/drivers")).map(mapDriver);
  },
  async createDriver(payload: DriverInput | Record<string, unknown>) {
    return mapDriver(
      await request<DriverDto>("/drivers", {
        method: "POST",
        body: JSON.stringify(driverPayload(payload)),
      }),
    );
  },
  async updateDriver(id: string, payload: DriverInput) {
    return mapDriver(
      await request<DriverDto>(`/drivers/${id}`, {
        method: "PUT",
        body: JSON.stringify(driverPayload(payload)),
      }),
    );
  },
  deleteDriver(id: string) {
    return request<void>(`/drivers/${id}`, { method: "DELETE" });
  },
  async completeOnboarding() {
    return mapOnboarding(
      await request<OnboardingDto>("/onboarding/complete", { method: "POST" }),
    );
  },
  async conversations() {
    return (await request<ConversationDto[]>("/conversations")).map(mapConversation);
  },
  async createConversation(title = "Новая заявка") {
    return mapConversation(
      await request<ConversationDto>("/conversations", {
        method: "POST",
        body: JSON.stringify({ title }),
      }),
    );
  },
  async updateConversation(
    conversationId: string,
    payload: { title?: string; status?: "active" | "archived" },
  ) {
    return mapConversation(
      await request<ConversationDto>(`/conversations/${conversationId}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    );
  },
  async messages(conversationId: string) {
    const detail = await request<ConversationDetailDto>(`/conversations/${conversationId}`);
    const attachmentIds = [
      ...new Set(detail.messages.flatMap((message) => message.attachments)),
    ];
    const metadataResults = await Promise.allSettled(
      attachmentIds.map((fileId) => request<FileDto>(`/files/${fileId}`)),
    );
    const metadata = new Map<string, FileDto>();
    for (const result of metadataResults) {
      if (result.status === "fulfilled") metadata.set(result.value.id, result.value);
    }
    return detail.messages.map((message) => mapMessage(message, metadata));
  },
  async sendMessage(conversationId: string, body: string, attachmentIds: string[] = []) {
    return mapMessage(
      await request<MessageDto>(`/conversations/${conversationId}/messages`, {
        method: "POST",
        body: JSON.stringify({ content: body, attachment_ids: attachmentIds }),
      }),
    );
  },
  async conversationWorkflow(conversationId: string) {
    try {
      return mapConversationWorkflow(
        await request<ConversationWorkflowDto>(
          `/conversations/${conversationId}/workflow`,
        ),
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },
  async retryConversationWorkflow(conversationId: string) {
    return mapConversationWorkflow(
      await request<ConversationWorkflowDto>(
        `/conversations/${conversationId}/workflow/retry`,
        { method: "POST" },
      ),
    );
  },
  async trips() {
    return (await request<TripDto[]>("/trips")).map(mapTrip);
  },
  async updateTrip(
    tripId: string,
    payload: {
      selectedVehicleId?: string | null;
      selectedDriverId?: string | null;
    },
  ) {
    return mapWorkflowTrip(
      await request<TripDto>(`/trips/${tripId}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...(payload.selectedVehicleId !== undefined
            ? { selected_vehicle_id: payload.selectedVehicleId }
            : {}),
          ...(payload.selectedDriverId !== undefined
            ? { selected_driver_id: payload.selectedDriverId }
            : {}),
        }),
      }),
    );
  },
  async confirmTrip(tripId: string) {
    return mapWorkflowTrip(
      await request<TripDto>(`/trips/${tripId}/confirm`, { method: "POST" }),
    );
  },
  async tripAutomation(tripId: string) {
    return mapTripAutomation(
      await request<TripAutomationDto>(`/trips/${tripId}/automation`),
    );
  },
  async files() {
    return (await request<FileDto[]>("/files")).map(mapFile);
  },
  async tripFiles(tripId: string) {
    const query = new URLSearchParams({
      trip_id: tripId,
      archive_only: "true",
    });
    return (await request<FileDto[]>(`/files?${query.toString()}`)).map(mapFile);
  },
  async fileMetadata(fileId: string) {
    return mapFile(await request<FileDto>(`/files/${fileId}`));
  },
  async downloadFile(fileId: string) {
    const response = await fetchResponse(`/files/${fileId}/download`);
    if (!response.ok) throw await responseError(response);
    const disposition = response.headers.get("content-disposition") ?? "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    let filename = encoded ? decodeURIComponent(encoded) : plain;
    if (!filename) filename = `buscript-${fileId}`;
    return { blob: await response.blob(), filename };
  },
  async billing(): Promise<BalanceSummary> {
    const [balance, transactions] = await Promise.all([
      request<BalanceDto>("/billing/balance"),
      request<TransactionDto[]>("/billing/transactions"),
    ]);
    const mappedTransactions: BalanceTransaction[] = transactions.map((item) => ({
      id: item.id,
      label:
        item.note ??
        (item.trip_reference ? `Уведомление ${item.trip_reference}` : item.kind),
      amount: Number(item.amount),
      createdAt: formatDateTime(item.created_at),
      actor:
        item.actor_name ??
        item.actor_email ??
        (item.actor_id ? "Пользователь" : "Автоматическая операция"),
    }));
    return {
      available: Number(balance.available),
      reserved: Number(balance.reserved),
      total: Number(balance.total),
      transactions: mappedTransactions,
    };
  },
  async uploadFile(file: File, conversationId?: string) {
    const data = new FormData();
    data.append("file", file);
    if (conversationId) data.append("conversation_id", conversationId);
    return mapFile(await request<FileDto>("/files", { method: "POST", body: data }));
  },
  async adminTenants() {
    return (await request<AdminTenantDto[]>("/admin/tenants")).map(mapAdminTenant);
  },
  async setTenantBalance(tenantId: string, available: number, note: string) {
    return request<AdminTenantDto | BalanceDto>(`/admin/tenants/${tenantId}/balance`, {
      method: "PATCH",
      body: JSON.stringify({ available_notifications: available, note: note || undefined }),
    });
  },
};
