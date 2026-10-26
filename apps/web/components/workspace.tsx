"use client";

import Link from "next/link";
import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Icon } from "@/components/icons";
import { ProgressBar } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type {
  ChatMessage,
  Conversation,
  ConversationWorkflow,
  Driver,
  FileEntry,
  MessageAttachment,
  MissingClarification,
  TripAutomation,
  Vehicle,
} from "@/lib/types";

type ConversationUpdate = {
  title?: string;
  status?: "active" | "archived";
};

type DownloadResult = {
  blob: Blob;
  filename: string;
};

type WorkspaceApi = typeof api & {
  createConversation(title: string): Promise<Conversation>;
  updateConversation(id: string, payload: ConversationUpdate): Promise<Conversation>;
  downloadFile(id: string): Promise<DownloadResult>;
};

type RefreshOptions = {
  surfaceErrors?: boolean;
};

type RecognizedFact = {
  key: string;
  label: string;
  value: string;
};

const workspaceApi = api as WorkspaceApi;

const PROCESSING_RUN_STATUSES = new Set(["queued", "extracting"]);
const PROCESSING_TRIP_STATUSES = new Set([
  "extracting",
  "confirmed",
  "generating",
  "generating_documents",
  "building_payload",
  "ready_to_submit",
  "submitting",
  "awaiting_captcha",
  "captcha_processing",
  "receipt_pending",
  "retry_scheduled",
]);
const LOCKED_TRIP_STATUSES = new Set([
  "confirmed",
  "generating",
  "generating_documents",
  "building_payload",
  "ready_to_submit",
  "submitting",
  "awaiting_captcha",
  "captcha_processing",
  "dry_run_ready",
  "submitted",
  "receipt_pending",
  "completed",
]);

const STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  uploaded: "Файлы загружены",
  extracting: "Распознаём данные",
  clarifying: "Нужны уточнения",
  needs_review: "Нужна проверка",
  ready: "Готово к подтверждению",
  validated: "Данные проверены",
  confirmed: "Подтверждено",
  generating: "Формируем документы",
  generating_documents: "Формируем документы",
  building_payload: "Готовим данные для ГИБДД",
  ready_to_submit: "Готово к заполнению",
  submitting: "Заполняем форму ГИБДД",
  awaiting_captcha: "Ожидаем капчу",
  captcha_processing: "Обрабатываем капчу",
  dry_run_ready: "Тестовое заполнение готово",
  submitted: "Уведомление подано",
  receipt_pending: "Ожидаем квитанцию",
  completed: "Готово",
  retry_scheduled: "Повторим автоматически",
  config_required: "Требуется настройка OpenRouter",
  failed: "Ошибка обработки",
  needs_support: "Нужна техническая помощь",
  cancelled: "Отменено",
};

const FIELD_LABELS: Record<string, string> = {
  customer: "Заказчик",
  customer_name: "Наименование заказчика",
  name: "Наименование",
  customer_type: "Тип заказчика",
  inn: "ИНН",
  customer_inn: "ИНН заказчика",
  address: "Адрес",
  customer_address: "Адрес заказчика",
  trip: "Поездка",
  trip_date: "Дата поездки",
  date: "Дата",
  start_time: "Начало",
  end_time: "Окончание",
  route: "Маршрут",
  route_text: "Маршрут",
  route_points: "Точки маршрута",
  region_codes: "Регионы",
  passenger_count: "Количество детей",
  children_count: "Количество детей",
  children: "Дети",
  companions: "Сопровождающие",
  transport_preferences: "Пожелания по транспорту",
  vehicle: "Автобус",
  driver: "Водитель",
};

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Требуется авторизация. Войдите в аккаунт и повторите действие.";
    }
    return error.message;
  }
  return "Не удалось выполнить запрос. Проверьте подключение к API.";
}

function formatFileSize(bytes: number) {
  if (bytes < 1_000_000) return `${Math.max(1, Math.round(bytes / 1_000))} КБ`;
  return `${(bytes / 1_000_000).toFixed(1)} МБ`;
}

function formatTripDate(value?: string) {
  if (!value) return "";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(date);
}

function formatUpdatedAt(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("ru", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function isArchived(conversation: Conversation) {
  return conversation.preview === "Диалог в архиве";
}

function isWorkflowProcessing(workflow: ConversationWorkflow | null) {
  if (!workflow) return false;
  return (
    PROCESSING_RUN_STATUSES.has(workflow.run?.status ?? "") ||
    PROCESSING_TRIP_STATUSES.has(workflow.trip.status)
  );
}

function isTripLocked(workflow: ConversationWorkflow | null) {
  return Boolean(workflow && LOCKED_TRIP_STATUSES.has(workflow.trip.status));
}

function statusClass(status: string) {
  if (["completed", "submitted", "dry_run_ready"].includes(status)) {
    return "status-success";
  }
  if (["failed", "needs_support"].includes(status)) return "status-danger";
  if (
    ["clarifying", "needs_review", "awaiting_captcha", "config_required"].includes(
      status,
    )
  ) {
    return "status-warning";
  }
  return "status-neutral";
}

function statusLabel(status: string) {
  return STATUS_LABELS[status] ?? status.replaceAll("_", " ");
}

function visibleWorkflowStatus(workflow: ConversationWorkflow) {
  return workflow.configurationRequired ? "config_required" : workflow.trip.status;
}

function saveDownload(result: DownloadResult) {
  const url = window.URL.createObjectURL(result.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = result.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(url), 1_000);
}

function readableFieldLabel(path: string) {
  const parts = path.split(".");
  return parts
    .map((part) => FIELD_LABELS[part] ?? part.replaceAll("_", " "))
    .join(" · ");
}

function scalarValue(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  return null;
}

function flattenExtractedFields(
  value: unknown,
  path = "",
  depth = 0,
): RecognizedFact[] {
  if (depth > 3 || value === null || value === undefined) return [];
  const scalar = scalarValue(value);
  if (scalar !== null && path) {
    return [{ key: path, label: readableFieldLabel(path), value: scalar }];
  }
  if (Array.isArray(value)) {
    if (!path || value.length === 0) return [];
    const simple = value.map(scalarValue);
    if (simple.every((item) => item !== null)) {
      return [
        {
          key: path,
          label: readableFieldLabel(path),
          value: simple.filter((item): item is string => item !== null).join(", "),
        },
      ];
    }
    return [
      {
        key: path,
        label: readableFieldLabel(path),
        value: `${value.length} ${value.length === 1 ? "запись" : "записей"}`,
      },
    ];
  }
  if (typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !["raw_text", "source_carrier_text", "slot_states"].includes(key))
    .flatMap(([key, child]) =>
      flattenExtractedFields(child, path ? `${path}.${key}` : key, depth + 1),
    );
}

function Attachment({
  item,
  onDownload,
}: {
  item: MessageAttachment;
  onDownload: (item: MessageAttachment) => void;
}) {
  const ready = item.status === "ready" && !item.id.startsWith("local-");
  const statusText = {
    uploading: "Загрузка…",
    ready: "Загружен",
    processing: "Обработка…",
    error: "Ошибка загрузки",
  }[item.status];

  return (
    <button
      className="message-attachment"
      disabled={!ready}
      onClick={() => onDownload(item)}
      type="button"
    >
      <span className="attachment-icon">
        <Icon name="document" size={19} />
        <small>{item.type}</small>
      </span>
      <span>
        <strong>{item.name}</strong>
        <small>
          {formatFileSize(item.size)} · {statusText}
        </small>
      </span>
      {ready && <Icon name="download" size={17} />}
    </button>
  );
}

export function Workspace() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [workflow, setWorkflow] = useState<ConversationWorkflow | null>(null);
  const [automation, setAutomation] = useState<TripAutomation | null>(null);
  const [artifacts, setArtifacts] = useState<FileEntry[]>([]);
  const [previewFiles, setPreviewFiles] = useState<FileEntry[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [vehicleChoice, setVehicleChoice] = useState("");
  const [driverChoice, setDriverChoice] = useState("");
  const [composer, setComposer] = useState("");
  const [uploads, setUploads] = useState<MessageAttachment[]>([]);
  const [conversationQuery, setConversationQuery] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [sending, setSending] = useState(false);
  const [awaitingReply, setAwaitingReply] = useState<{
    conversationId: string;
    messageId: string;
  } | null>(null);
  const [savingAssignment, setSavingAssignment] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const messageEndRef = useRef<HTMLDivElement>(null);
  const activeIdRef = useRef<string | null>(null);
  const pollFailureCount = useRef(0);
  const awaitingDeadlineRef = useRef(0);

  const activeConversation = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [activeId, conversations],
  );

  const visibleConversations = useMemo(() => {
    const normalized = conversationQuery.trim().toLowerCase();
    return conversations.filter((conversation) => {
      const matchesArchive = isArchived(conversation) === showArchived;
      const matchesQuery =
        !normalized ||
        conversation.title.toLowerCase().includes(normalized) ||
        conversation.preview.toLowerCase().includes(normalized);
      return matchesArchive && matchesQuery;
    });
  }, [conversationQuery, conversations, showArchived]);

  const attachmentCount = useMemo(
    () =>
      messages.reduce(
        (total, message) => total + (message.attachments?.length ?? 0),
        0,
      ),
    [messages],
  );

  const activeVehicles = useMemo(
    () => vehicles.filter((item) => item.isActive),
    [vehicles],
  );
  const activeDrivers = useMemo(
    () => drivers.filter((item) => item.isActive),
    [drivers],
  );

  const assignmentDirty = Boolean(
    workflow &&
      (vehicleChoice !== (workflow.trip.selectedVehicleId ?? "") ||
        driverChoice !== (workflow.trip.selectedDriverId ?? "")),
  );

  const recognizedFacts = useMemo(() => {
    if (!workflow) return [];
    const extracted = flattenExtractedFields(workflow.extractedFields);
    if (extracted.length > 0) return extracted.slice(0, 14);

    const trip = workflow.trip;
    return [
      trip.customerName
        ? { key: "customer_name", label: "Заказчик", value: trip.customerName }
        : null,
      trip.customerInn
        ? { key: "customer_inn", label: "ИНН заказчика", value: trip.customerInn }
        : null,
      trip.tripDate
        ? { key: "trip_date", label: "Дата поездки", value: formatTripDate(trip.tripDate) }
        : null,
      trip.startTime
        ? { key: "start_time", label: "Начало", value: trip.startTime }
        : null,
      trip.endTime ? { key: "end_time", label: "Окончание", value: trip.endTime } : null,
      trip.routeText
        ? { key: "route_text", label: "Маршрут", value: trip.routeText }
        : null,
      trip.passengerCount !== undefined
        ? {
            key: "passenger_count",
            label: "Количество детей",
            value: String(trip.passengerCount),
          }
        : null,
    ].filter((item): item is RecognizedFact => item !== null);
  }, [workflow]);

  const missingFields = useMemo(
    () => new Set(workflow?.missingClarifications.map((item) => item.field) ?? []),
    [workflow],
  );

  const confirmationPreview = useMemo(
    () =>
      previewFiles.find(
        (file) =>
          file.kind === "png" && file.name.toLowerCase().includes("confirmation"),
      ) ?? previewFiles.find((file) => file.kind === "png"),
    [previewFiles],
  );

  const primaryNotification = useMemo(
    () =>
      artifacts.find(
        (file) => file.artifactKind === "notification" && file.kind === "pdf",
      ) ??
      artifacts.find((file) => file.artifactKind === "notification") ??
      artifacts[0],
    [artifacts],
  );

  const refreshActiveData = useCallback(
    async (conversationId: string, options: RefreshOptions = {}) => {
      const [messageResult, workflowResult] = await Promise.allSettled([
        api.messages(conversationId),
        api.conversationWorkflow(conversationId),
      ]);
      if (activeIdRef.current !== conversationId) return;

      const errors: string[] = [];
      if (messageResult.status === "fulfilled") {
        const nextMessages = messageResult.value;
        setMessages(nextMessages);
        if (
          awaitingReply &&
          awaitingReply.conversationId === conversationId
        ) {
          const sentIndex = nextMessages.findIndex(
            (message) => message.id === awaitingReply.messageId,
          );
          const hasCompletedAssistantReply =
            sentIndex >= 0 &&
            nextMessages.slice(sentIndex + 1).some(
              (message) =>
                message.author === "assistant" &&
                !(
                  message.kind === "assistant_processing" &&
                  message.metadata?.status === "processing"
                ),
            );
          if (hasCompletedAssistantReply || Date.now() >= awaitingDeadlineRef.current) {
            setAwaitingReply(null);
          }
        }
      } else {
        errors.push(getErrorMessage(messageResult.reason));
      }

      if (workflowResult.status === "fulfilled") {
        const nextWorkflow = workflowResult.value;
        setWorkflow(nextWorkflow);
        if (nextWorkflow) {
          setVehicleChoice(nextWorkflow.trip.selectedVehicleId ?? "");
          setDriverChoice(nextWorkflow.trip.selectedDriverId ?? "");
          const [artifactResult, automationResult] = await Promise.allSettled([
            api.tripFiles(nextWorkflow.trip.id),
            api.tripAutomation(nextWorkflow.trip.id),
          ]);
          if (activeIdRef.current !== conversationId) return;
          if (artifactResult.status === "fulfilled") {
            setArtifacts(artifactResult.value);
          } else {
            errors.push(getErrorMessage(artifactResult.reason));
          }
          if (automationResult.status === "fulfilled") {
            const nextAutomation = automationResult.value;
            setAutomation(nextAutomation);
            const diagnosticIds = [
              ...new Set(
                nextAutomation.attempts.flatMap((attempt) => attempt.diagnosticFileIds),
              ),
            ];
            const diagnosticResults = await Promise.allSettled(
              diagnosticIds.map((fileId) => api.fileMetadata(fileId)),
            );
            if (activeIdRef.current === conversationId) {
              setPreviewFiles(
                diagnosticResults.flatMap((result) =>
                  result.status === "fulfilled" ? [result.value] : [],
                ),
              );
            }
          } else {
            errors.push(getErrorMessage(automationResult.reason));
          }
        } else {
          setAutomation(null);
          setArtifacts([]);
          setPreviewFiles([]);
        }
      } else {
        errors.push(getErrorMessage(workflowResult.reason));
      }

      if (errors.length === 0) {
        pollFailureCount.current = 0;
      } else if (options.surfaceErrors) {
        setActionError([...new Set(errors)].join(" "));
      } else {
        pollFailureCount.current += 1;
        if (pollFailureCount.current >= 3) {
          setActionError(
            `Не удалось обновить статус обработки: ${[...new Set(errors)].join(" ")}`,
          );
        }
      }
      setLoadingMessages(false);
      setWorkflowLoading(false);
    },
    [awaitingReply],
  );

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([api.conversations(), api.vehicles(), api.drivers()])
      .then(([conversationResult, vehicleResult, driverResult]) => {
        if (cancelled) return;
        const errors: string[] = [];
        if (conversationResult.status === "fulfilled") {
          const rows = conversationResult.value;
          setConversations(rows);
          if (rows.some((conversation) => !isArchived(conversation))) {
            setLoadingMessages(true);
            setWorkflowLoading(true);
          }
          setActiveId((current) => {
            if (current && rows.some((conversation) => conversation.id === current)) {
              return current;
            }
            return rows.find((conversation) => !isArchived(conversation))?.id ?? null;
          });
        } else {
          setConversations([]);
          errors.push(getErrorMessage(conversationResult.reason));
        }
        if (vehicleResult.status === "fulfilled") {
          setVehicles(vehicleResult.value);
        } else {
          errors.push(getErrorMessage(vehicleResult.reason));
        }
        if (driverResult.status === "fulfilled") {
          setDrivers(driverResult.value);
        } else {
          errors.push(getErrorMessage(driverResult.reason));
        }
        setLoadError(errors.length > 0 ? [...new Set(errors)].join(" ") : null);
      })
      .finally(() => {
        if (!cancelled) setLoadingConversations(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    const timer = window.setTimeout(() => {
      void refreshActiveData(activeId, { surfaceErrors: true });
    }, 0);
    return () => window.clearTimeout(timer);
  }, [activeId, refreshActiveData]);

  const chatIntentProcessing = messages.some(
    (message) =>
      message.kind === "assistant_processing" &&
      message.metadata?.status === "processing",
  );
  const shouldPoll = Boolean(
    activeId &&
      ((awaitingReply && awaitingReply.conversationId === activeId) ||
        isWorkflowProcessing(workflow) ||
        chatIntentProcessing),
  );

  useEffect(() => {
    if (!activeId || !shouldPoll) return;
    let cancelled = false;
    let requestRunning = false;
    const timer = window.setInterval(() => {
      if (cancelled || requestRunning) return;
      requestRunning = true;
      void refreshActiveData(activeId).finally(() => {
        requestRunning = false;
      });
    }, 1_500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeId, refreshActiveData, shouldPoll]);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function createConversation() {
    if (creating) return;
    setCreating(true);
    setActionError(null);
    try {
      const created = await workspaceApi.createConversation("Новая заявка");
      setConversations((current) => [
        created,
        ...current.filter((conversation) => conversation.id !== created.id),
      ]);
      setShowArchived(false);
      setActiveId(created.id);
      setMessages([]);
      setWorkflow(null);
      setAutomation(null);
      setArtifacts([]);
      setPreviewFiles([]);
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setCreating(false);
    }
  }

  function toggleArchiveList() {
    const nextShowArchived = !showArchived;
    setShowArchived(nextShowArchived);
    setConversationQuery("");
    setRenaming(false);
    const nextConversation = conversations.find(
      (conversation) => isArchived(conversation) === nextShowArchived,
    );
    setMessages([]);
    setWorkflow(null);
    setAutomation(null);
    setArtifacts([]);
    setPreviewFiles([]);
    setLoadingMessages(Boolean(nextConversation));
    setWorkflowLoading(Boolean(nextConversation));
    setActiveId(nextConversation?.id ?? null);
  }

  async function saveTitle(event: FormEvent) {
    event.preventDefault();
    if (!activeConversation) return;
    const title = renameValue.trim();
    if (!title || title === activeConversation.title) {
      setRenaming(false);
      return;
    }
    setActionError(null);
    try {
      const updated = await workspaceApi.updateConversation(activeConversation.id, { title });
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === updated.id ? updated : conversation,
        ),
      );
      setRenaming(false);
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }

  async function toggleConversationArchive() {
    if (!activeConversation) return;
    const nextStatus = isArchived(activeConversation) ? "active" : "archived";
    setActionError(null);
    try {
      const updated = await workspaceApi.updateConversation(activeConversation.id, {
        status: nextStatus,
      });
      const nextConversations = conversations.map((conversation) =>
        conversation.id === updated.id ? updated : conversation,
      );
      setConversations(nextConversations);
      const nextActive = nextConversations.find(
        (conversation) =>
          conversation.id !== updated.id &&
          isArchived(conversation) === showArchived,
      );
      setLoadingMessages(Boolean(nextActive));
      setWorkflowLoading(Boolean(nextActive));
      setActiveId(nextActive?.id ?? null);
      setMessages([]);
      setWorkflow(null);
      setAutomation(null);
      setArtifacts([]);
      setPreviewFiles([]);
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }

  function addFiles(files: FileList | File[]) {
    if (!activeConversation) {
      setActionError("Сначала создайте или выберите диалог.");
      return;
    }

    const selectedFiles = Array.from(files);
    const nextFiles = selectedFiles.map<MessageAttachment>((file, index) => ({
      id: `local-${Date.now()}-${index}`,
      name: file.name,
      type: file.name.split(".").pop()?.toUpperCase() ?? "FILE",
      size: file.size,
      status: "uploading",
    }));
    setUploads((current) => [...current, ...nextFiles]);
    setActionError(null);

    selectedFiles.forEach((file, index) => {
      const localId = nextFiles[index].id;
      void api
        .uploadFile(file, activeConversation.id)
        .then((uploaded) => {
          setUploads((current) =>
            current.map((item) =>
              item.id === localId ? { ...item, id: uploaded.id, status: "ready" } : item,
            ),
          );
        })
        .catch((error: unknown) => {
          setUploads((current) =>
            current.map((item) =>
              item.id === localId ? { ...item, status: "error" } : item,
            ),
          );
          setActionError(getErrorMessage(error));
        });
    });
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) addFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) addFiles(event.dataTransfer.files);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const body = composer.trim();
    const hasPendingUpload = uploads.some((file) => file.status === "uploading");
    const hasFailedUpload = uploads.some((file) => file.status === "error");
    if (
      !activeConversation ||
      (!body && uploads.length === 0) ||
      sending ||
      hasPendingUpload ||
      hasFailedUpload
    ) {
      return;
    }

    const conversationId = activeConversation.id;
    const attachmentIds = uploads
      .filter((file) => file.status === "ready" && !file.id.startsWith("local-"))
      .map((file) => file.id);
    setSending(true);
    setActionError(null);
    try {
      const saved = await api.sendMessage(conversationId, body, attachmentIds);
      if (activeIdRef.current === conversationId) {
        awaitingDeadlineRef.current = Date.now() + 120_000;
        setAwaitingReply({ conversationId, messageId: saved.id });
        setMessages((current) => [
          ...current,
          { ...saved, attachments: uploads.length > 0 ? uploads : undefined },
        ]);
        setComposer("");
        setUploads([]);
      }
      window.dispatchEvent(new Event("buscript:trips-updated"));
      try {
        const conversationRows = await api.conversations();
        setConversations(conversationRows);
      } catch {
        setActionError(
          "Сообщение сохранено, но список диалогов не обновился. История восстановится после перезагрузки.",
        );
      }
      await refreshActiveData(conversationId, { surfaceErrors: true });
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setSending(false);
    }
  }

  async function downloadAttachment(item: MessageAttachment) {
    setActionError(null);
    try {
      saveDownload(await workspaceApi.downloadFile(item.id));
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }

  async function downloadArtifact(file: FileEntry) {
    setActionError(null);
    try {
      saveDownload(await workspaceApi.downloadFile(file.id));
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }

  async function openPreview(file: FileEntry) {
    const previewWindow = window.open("about:blank", "_blank");
    if (previewWindow) previewWindow.opener = null;
    setActionError(null);
    try {
      const result = await workspaceApi.downloadFile(file.id);
      const url = window.URL.createObjectURL(result.blob);
      if (previewWindow) {
        previewWindow.location.replace(url);
        window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
      } else {
        saveDownload(result);
      }
    } catch (error) {
      previewWindow?.close();
      setActionError(getErrorMessage(error));
    }
  }

  function startRenaming() {
    if (!activeConversation) return;
    setRenameValue(activeConversation.title);
    setRenaming(true);
  }

  function prepareClarificationAnswer(question: MissingClarification) {
    const answer = question.suggestedValue
      ? `${question.label}: ${question.suggestedValue}`
      : "";
    setComposer(answer);
    window.setTimeout(() => composerRef.current?.focus(), 0);
  }

  async function saveAssignment() {
    if (!workflow || !assignmentDirty || savingAssignment) return;
    setSavingAssignment(true);
    setActionError(null);
    try {
      const updated = await api.updateTrip(workflow.trip.id, {
        selectedVehicleId: vehicleChoice || null,
        selectedDriverId: driverChoice || null,
      });
      setWorkflow((current) => (current ? { ...current, trip: updated } : current));
      await refreshActiveData(activeIdRef.current ?? "", { surfaceErrors: true });
      window.dispatchEvent(new Event("buscript:trips-updated"));
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setSavingAssignment(false);
    }
  }

  async function confirmTrip() {
    if (!workflow || confirming || assignmentDirty) return;
    setConfirming(true);
    setActionError(null);
    try {
      const updated = await api.confirmTrip(workflow.trip.id);
      setWorkflow((current) => (current ? { ...current, trip: updated } : current));
      window.dispatchEvent(new Event("buscript:trips-updated"));
      if (activeIdRef.current) {
        await refreshActiveData(activeIdRef.current, { surfaceErrors: true });
      }
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setConfirming(false);
    }
  }

  async function retryWorkflow() {
    if (!activeConversation || retrying) return;
    setRetrying(true);
    setActionError(null);
    try {
      const next = await api.retryConversationWorkflow(activeConversation.id);
      setWorkflow(next);
      await refreshActiveData(activeConversation.id, { surfaceErrors: true });
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setRetrying(false);
    }
  }

  const processing = isWorkflowProcessing(workflow);
  const tripLocked = isTripLocked(workflow);
  const assignmentLocked = tripLocked || processing;
  const canConfirm =
    Boolean(workflow?.canConfirm) &&
    Boolean(vehicleChoice) &&
    Boolean(driverChoice) &&
    !assignmentDirty &&
    !tripLocked;

  return (
    <div className="workspace">
      <aside className="conversation-rail">
        <div className="conversation-rail-head">
          <div>
            <span className="eyebrow">{showArchived ? "Архив" : "Диалоги"}</span>
            <h1>Ассистент</h1>
          </div>
          <button
            aria-label="Новый диалог"
            className="icon-button icon-button-dark"
            disabled={creating}
            onClick={() => void createConversation()}
            type="button"
          >
            <Icon name={creating ? "refresh" : "plus"} size={18} />
          </button>
        </div>

        <label className="search-field compact">
          <Icon name="search" size={17} />
          <input
            aria-label="Поиск диалогов"
            onChange={(event) => setConversationQuery(event.target.value)}
            placeholder="Найти диалог"
            value={conversationQuery}
          />
        </label>

        <div className="conversation-list">
          <span className="list-caption">{showArchived ? "Архивные" : "Активные"}</span>
          {loadingConversations ? (
            <div className="workspace-list-state">Загружаем диалоги…</div>
          ) : visibleConversations.length > 0 ? (
            visibleConversations.map((conversation) => (
              <button
                className={`conversation-row ${conversation.id === activeId ? "is-active" : ""}`}
                key={conversation.id}
                onClick={() => {
                  if (conversation.id !== activeId) {
                    setMessages([]);
                    setWorkflow(null);
                    setAutomation(null);
                    setArtifacts([]);
                    setPreviewFiles([]);
                    setActionError(null);
                    setLoadingMessages(true);
                    setWorkflowLoading(true);
                  }
                  setActiveId(conversation.id);
                  setRenaming(false);
                }}
                type="button"
              >
                <span className="conversation-status" />
                <span>
                  <strong>{conversation.title}</strong>
                  <small>{conversation.preview}</small>
                </span>
                <time>{conversation.updatedAt}</time>
              </button>
            ))
          ) : (
            <div className="workspace-list-state">
              {conversationQuery
                ? "По вашему запросу ничего не найдено."
                : showArchived
                  ? "Архив пуст."
                  : "Диалогов пока нет."}
            </div>
          )}
        </div>

        <button className="rail-archive" onClick={toggleArchiveList} type="button">
          <Icon name={showArchived ? "back" : "clock"} size={17} />
          {showArchived ? "Вернуться к активным" : "Архив диалогов"}
          <Icon name="chevron" size={15} />
        </button>
      </aside>

      <section className="conversation-stage">
        <header className="conversation-header">
          <div>
            {renaming && activeConversation ? (
              <form className="conversation-rename-form" onSubmit={saveTitle}>
                <input
                  aria-label="Название диалога"
                  autoFocus
                  maxLength={255}
                  onChange={(event) => setRenameValue(event.target.value)}
                  value={renameValue}
                />
                <button className="button button-dark button-small" type="submit">
                  Сохранить
                </button>
                <button
                  className="button button-secondary button-small"
                  onClick={() => setRenaming(false)}
                  type="button"
                >
                  Отмена
                </button>
              </form>
            ) : (
              <>
                <div className="conversation-title-line">
                  <h2>{activeConversation?.title ?? "Диалог не выбран"}</h2>
                  {workflow ? (
                    <span
                      className={`status-badge ${statusClass(
                        visibleWorkflowStatus(workflow),
                      )}`}
                    >
                      <span className="status-dot" />
                      {statusLabel(visibleWorkflowStatus(workflow))}
                    </span>
                  ) : activeConversation ? (
                    <span
                      className={`status-badge ${
                        isArchived(activeConversation) ? "status-neutral" : "status-success"
                      }`}
                    >
                      <span className="status-dot" />
                      {isArchived(activeConversation) ? "В архиве" : "Активен"}
                    </span>
                  ) : null}
                </div>
                <p>
                  {workflow
                    ? `${workflow.trip.reference} · обновлено ${formatUpdatedAt(
                        workflow.trip.updatedAt,
                      )}`
                    : activeConversation
                      ? `Обновлён ${activeConversation.updatedAt}`
                      : "Создайте диалог, чтобы загрузить заявку и начать работу."}
                </p>
              </>
            )}
          </div>
          <div className="conversation-actions">
            {activeConversation && !renaming && (
              <>
                <button
                  className="button button-secondary button-small"
                  onClick={startRenaming}
                  type="button"
                >
                  Переименовать
                </button>
                <button
                  className="button button-secondary button-small"
                  onClick={() => void toggleConversationArchive()}
                  type="button"
                >
                  {isArchived(activeConversation) ? "Вернуть" : "В архив"}
                </button>
              </>
            )}
            <button
              aria-label="Показать сведения о заявке"
              className="icon-button inspector-toggle"
              disabled={!activeConversation}
              onClick={() => setInspectorOpen((value) => !value)}
              type="button"
            >
              <Icon name="list" size={19} />
            </button>
          </div>
        </header>

        {loadError && (
          <div className="view-error-banner" role="alert">
            <Icon name="alert" size={17} />
            <span>{loadError}</span>
          </div>
        )}

        {workflow && (
          <div
            aria-live="polite"
            className={`workflow-strip ${processing ? "is-processing" : ""}`}
            role="status"
          >
            <div>
              <span className="workflow-strip-icon">
                <Icon
                  name={
                    processing
                      ? "refresh"
                      : workflow.configurationRequired ||
                          ["failed", "needs_support"].includes(workflow.trip.status)
                        ? "alert"
                        : "check"
                  }
                  size={15}
                />
              </span>
              <span>
                <strong>{statusLabel(visibleWorkflowStatus(workflow))}</strong>
                <small>
                  {workflow.run?.errorMessage ??
                    workflow.trip.failureReason ??
                    automation?.lastError ??
                    workflow.summary ??
                    `Текущий прогресс сохранён в заявке ${workflow.trip.reference}`}
                </small>
              </span>
            </div>
            <strong>{workflow.trip.progress}%</strong>
          </div>
        )}

        <div
          aria-live="polite"
          aria-relevant="additions text"
          className="message-scroll"
          role="log"
        >
          {actionError && (
            <div className="view-error-banner" role="alert">
              <Icon name="alert" size={17} />
              <span>{actionError}</span>
              <button
                aria-label="Закрыть сообщение"
                onClick={() => setActionError(null)}
                type="button"
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          )}

          {loadingMessages ? (
            <div className="workspace-empty-state">
              <Icon name="refresh" size={22} />
              <strong>Загружаем историю…</strong>
            </div>
          ) : !activeConversation ? (
            <div className="workspace-empty-state">
              <Icon name="message" size={25} />
              <strong>Начните с нового диалога</strong>
              <span>Все сообщения, документы и статусы будут сохранены в вашем аккаунте.</span>
              <button
                className="button button-dark"
                disabled={creating}
                onClick={() => void createConversation()}
                type="button"
              >
                Создать диалог
                <Icon name="plus" size={16} />
              </button>
            </div>
          ) : messages.length === 0 ? (
            <div className="workspace-empty-state">
              <Icon name="message" size={25} />
              <strong>Опишите поездку</strong>
              <span>
                Приложите заявку и список детей или отправьте сведения обычным сообщением.
              </span>
            </div>
          ) : (
            <>
              <div className="date-divider">
                <span>История диалога</span>
              </div>
              {messages.map((message) => (
                <article
                  className={`chat-message message-${message.author} ${
                    message.kind ? `message-kind-${message.kind}` : ""
                  }`}
                  key={message.id}
                >
                  {message.author !== "user" && (
                    <span className="assistant-avatar">
                      {message.author === "system" ? <Icon name="refresh" size={13} /> : "B"}
                    </span>
                  )}
                  <div className="message-content">
                    <div className="message-meta">
                      <strong>
                        {message.author === "user"
                          ? "Вы"
                          : message.author === "system"
                            ? "Статус системы"
                            : "Buscript"}
                      </strong>
                      <time>{message.createdAt}</time>
                    </div>
                    <p>{message.body}</p>
                    {message.attachments && message.attachments.length > 0 && (
                      <div className="attachment-list">
                        {message.attachments.map((attachment) => (
                          <Attachment
                            item={attachment}
                            key={attachment.id}
                            onDownload={(item) => void downloadAttachment(item)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              ))}
            </>
          )}

          {sending && (
            <article className="chat-message message-user is-saving">
              <div className="typing-indicator" aria-label="Сообщение сохраняется">
                <span />
                <span />
                <span />
              </div>
            </article>
          )}
          <div ref={messageEndRef} />
        </div>

        {activeConversation && !isArchived(activeConversation) && (
          <form className="composer-wrap" id="composer" onSubmit={handleSubmit}>
            {uploads.length > 0 && (
              <div className="composer-files">
                {uploads.map((file) => (
                  <span className={file.status === "error" ? "is-error" : ""} key={file.id}>
                    <Icon name="document" size={15} />
                    {file.name}
                    {file.status === "uploading" && " · загрузка…"}
                    {file.status === "error" && " · ошибка"}
                    <button
                      aria-label={`Удалить ${file.name}`}
                      onClick={() =>
                        setUploads((current) => current.filter((item) => item.id !== file.id))
                      }
                      type="button"
                    >
                      <Icon name="close" size={13} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div
              className={`composer ${dragging ? "is-dragging" : ""}`}
              onDragEnter={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false);
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDrop}
            >
              <button
                aria-label="Прикрепить файлы"
                className="composer-attach"
                onClick={() => fileInputRef.current?.click()}
                type="button"
              >
                <Icon name="paperclip" size={20} />
              </button>
              <input
                accept=".doc,.docx,.pdf,.xls,.xlsx,.csv,.png,.jpg,.jpeg"
                hidden
                multiple
                onChange={handleFileInput}
                ref={fileInputRef}
                type="file"
              />
              <textarea
                aria-label="Сообщение"
                onChange={(event) => setComposer(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder={
                  dragging
                    ? "Отпустите файлы здесь"
                    : workflow?.missingClarifications.length
                      ? "Ответьте на уточнение ассистента…"
                      : "Опишите поездку или прикрепите документы…"
                }
                ref={composerRef}
                rows={1}
                value={composer}
              />
              <button
                aria-label="Отправить сообщение"
                className="composer-send"
                disabled={
                  (!composer.trim() && uploads.length === 0) ||
                  sending ||
                  uploads.some((file) => ["uploading", "error"].includes(file.status))
                }
                type="submit"
              >
                <Icon name="send" size={18} />
              </button>
            </div>
            <p>Данные и ответы сохраняются в заявке · Enter — отправить</p>
          </form>
        )}
      </section>

      <aside className={`request-inspector ${inspectorOpen ? "is-open" : ""}`}>
        <div className="inspector-head">
          <div>
            <span className="eyebrow">{workflow ? "Заявка" : "Сведения"}</span>
            <h2>{workflow?.trip.reference ?? activeConversation?.title ?? "Диалог"}</h2>
          </div>
          <button
            aria-label="Закрыть сведения"
            className="icon-button inspector-close"
            onClick={() => setInspectorOpen(false)}
            type="button"
          >
            <Icon name="close" size={18} />
          </button>
        </div>

        {workflowLoading ? (
          <div className="workspace-list-state">Загружаем состояние заявки…</div>
        ) : workflow ? (
          <>
            <div className="inspector-scroll">
              <section className="inspector-section">
                <div className="section-line">
                  <span>Обработка</span>
                  <strong>{workflow.trip.progress}%</strong>
                </div>
                <ProgressBar value={workflow.trip.progress} size="compact" />
                <div className="inspector-status-line">
                  <span
                    className={`status-badge ${statusClass(
                      visibleWorkflowStatus(workflow),
                    )}`}
                  >
                    <span className="status-dot" />
                    {statusLabel(visibleWorkflowStatus(workflow))}
                  </span>
                  {workflow.run && <small>Ревизия {workflow.run.revision}</small>}
                </div>
                {(workflow.run?.errorMessage ||
                  workflow.trip.failureReason ||
                  automation?.lastError) && (
                  <p className="inspector-inline-error">
                    {workflow.run?.errorMessage ??
                      workflow.trip.failureReason ??
                      automation?.lastError}
                  </p>
                )}
                {workflow.configurationRequired && (
                  <p className="configuration-note">
                    Добавьте <code>OPENROUTER_API_KEY</code> в локальный <code>.env</code>,
                    затем нажмите «Проверить подключение». Ключ не вводится в браузере и не
                    сохраняется в профиле.
                  </p>
                )}
              </section>

              {workflow.missingClarifications.length > 0 && (
                <section className="inspector-section">
                  <div className="section-line">
                    <span>Нужно уточнить</span>
                    <strong>{workflow.missingClarifications.length}</strong>
                  </div>
                  <div className="clarification-list">
                    {workflow.missingClarifications.map((item) => (
                      <div key={`${item.field}-${item.question}`}>
                        <strong>{item.label}</strong>
                        <p>{item.question}</p>
                        {item.reason && <small>{item.reason}</small>}
                        <button
                          className="text-button"
                          onClick={() => prepareClarificationAnswer(item)}
                          type="button"
                        >
                          {item.suggestedValue
                            ? `Ответить «${item.suggestedValue}»`
                            : "Ответить в чате"}
                        </button>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <section className="inspector-section">
                <div className="section-line">
                  <span>Распознанные данные</span>
                  <strong>{recognizedFacts.length}</strong>
                </div>
                {recognizedFacts.length > 0 ? (
                  <dl className="recognized-list">
                    {recognizedFacts.map((fact) => (
                      <div
                        className={
                          missingFields.has(fact.key) ||
                          missingFields.has(fact.key.split(".").at(-1) ?? "")
                            ? "field-missing"
                            : ""
                        }
                        key={fact.key}
                      >
                        <dt>{fact.label}</dt>
                        <dd>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="inspector-empty-copy">
                    Распознанные поля появятся после обработки сообщения или файла.
                  </p>
                )}
              </section>

              <section className="inspector-section">
                <div className="section-line">
                  <span>Автобус и водитель</span>
                  {assignmentDirty && <small className="unsaved-label">Не сохранено</small>}
                </div>
                <div className="assignment-fields">
                  <label>
                    <span>Автобус</span>
                    <select
                      disabled={assignmentLocked || savingAssignment}
                      onChange={(event) => setVehicleChoice(event.target.value)}
                      value={vehicleChoice}
                    >
                      <option value="">Выберите автобус</option>
                      {activeVehicles.map((vehicle) => (
                        <option key={vehicle.id} value={vehicle.id}>
                          {vehicle.makeModel} · {vehicle.registrationPlate}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Водитель</span>
                    <select
                      disabled={assignmentLocked || savingAssignment}
                      onChange={(event) => setDriverChoice(event.target.value)}
                      value={driverChoice}
                    >
                      <option value="">Выберите водителя</option>
                      {activeDrivers.map((driver) => (
                        <option key={driver.id} value={driver.id}>
                          {driver.fullName}
                        </option>
                      ))}
                    </select>
                  </label>
                  {!assignmentLocked && (
                    <button
                      className="button button-secondary button-small button-full"
                      disabled={!assignmentDirty || savingAssignment}
                      onClick={() => void saveAssignment()}
                      type="button"
                    >
                      {savingAssignment ? "Сохраняем…" : "Сохранить назначение"}
                    </button>
                  )}
                </div>
                {(activeVehicles.length === 0 || activeDrivers.length === 0) && (
                  <p className="inspector-empty-copy">
                    Добавьте недостающий транспорт или водителя в{" "}
                    <Link href="/profile">настройках профиля</Link>.
                  </p>
                )}
              </section>

              <section className="inspector-section">
                <div className="section-line">
                  <span>Документы</span>
                  <strong>{artifacts.length}</strong>
                </div>
                {confirmationPreview && (
                  <button
                    className="dry-run-preview"
                    onClick={() => void openPreview(confirmationPreview)}
                    type="button"
                  >
                    <span>
                      <Icon name="check" size={16} />
                    </span>
                    <span>
                      <strong>Результат заполнения</strong>
                      <small>Открыть контрольный снимок формы ГИБДД</small>
                    </span>
                    <Icon name="chevron" size={14} />
                  </button>
                )}
                {artifacts.length > 0 ? (
                  <div className="inspector-documents">
                    {artifacts.map((file) => (
                      <button
                        className="inspector-document"
                        key={file.id}
                        onClick={() => void downloadArtifact(file)}
                        type="button"
                      >
                        <span>
                          <Icon name="document" size={15} />
                        </span>
                        <span>
                          <strong>{file.name}</strong>
                          <small>
                            {file.artifactKind === "receipt"
                              ? "Квитанция"
                              : "Уведомление"}
                          </small>
                        </span>
                        <Icon name="download" size={14} />
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="inspector-empty-copy">
                    Документы появятся здесь после подтверждения и генерации.
                  </p>
                )}
                {automation?.jobId && (
                  <dl className="automation-facts">
                    <div>
                      <dt>Режим</dt>
                      <dd>{automation.liveSubmission ? "Рабочая подача" : "Без отправки"}</dd>
                    </div>
                    <div>
                      <dt>Попыток заполнения</dt>
                      <dd>{automation.attemptCount}</dd>
                    </div>
                    <div>
                      <dt>Финальная отправка</dt>
                      <dd>{automation.finalSubmitAttempted ? "Выполнена" : "Заблокирована"}</dd>
                    </div>
                  </dl>
                )}
              </section>
            </div>

            <div className="inspector-footer">
              {["failed", "needs_support"].includes(workflow.trip.status) ||
              ["failed", "config_required"].includes(workflow.run?.status ?? "") ? (
                <button
                  className="button button-dark button-full"
                  disabled={retrying}
                  onClick={() => void retryWorkflow()}
                  type="button"
                >
                  {retrying
                    ? "Проверяем…"
                    : workflow.configurationRequired
                      ? "Проверить подключение"
                      : "Повторить обработку"}
                </button>
              ) : tripLocked ? (
                <>
                  <div className="blocked-note">
                    <span>
                      <strong>{statusLabel(workflow.trip.status)}</strong>
                      <small>
                        {workflow.trip.status === "dry_run_ready"
                          ? "Финальная отправка заблокирована безопасным режимом."
                          : "Состояние сохранено и обновляется автоматически."}
                      </small>
                    </span>
                  </div>
                  {primaryNotification && (
                    <button
                      className="button button-dark button-full"
                      onClick={() => void downloadArtifact(primaryNotification)}
                      type="button"
                    >
                      Скачать уведомление
                    </button>
                  )}
                </>
              ) : (
                <>
                  {!workflow.canConfirm && (
                    <p className="confirm-help">
                      Ответьте на вопросы и заполните обязательные данные перед подтверждением.
                    </p>
                  )}
                  <button
                    className="button button-dark button-full"
                    disabled={!canConfirm || confirming}
                    onClick={() => void confirmTrip()}
                    type="button"
                  >
                    {confirming ? "Подтверждаем…" : "Подтвердить заявку"}
                  </button>
                </>
              )}
              <Link className="text-button inspector-link" href="/requests">
                Открыть список поездок
              </Link>
            </div>
          </>
        ) : activeConversation ? (
          <>
            <section className="inspector-section">
              <div className="section-line">
                <span>Диалог</span>
                <strong>{isArchived(activeConversation) ? "Архив" : "Активен"}</strong>
              </div>
              <dl className="conversation-facts">
                <div>
                  <dt>Обновление</dt>
                  <dd>{activeConversation.updatedAt}</dd>
                </div>
                <div>
                  <dt>Сообщений</dt>
                  <dd>{messages.length}</dd>
                </div>
                <div>
                  <dt>Вложений</dt>
                  <dd>{attachmentCount}</dd>
                </div>
              </dl>
              <p className="inspector-empty-copy">
                Заявка появится после первого отправленного сообщения или файла.
              </p>
            </section>
            <div className="inspector-footer conversation-inspector-actions">
              <button
                className="button button-secondary button-full"
                onClick={startRenaming}
                type="button"
              >
                Переименовать
              </button>
              <button
                className="button button-dark button-full"
                onClick={() => void toggleConversationArchive()}
                type="button"
              >
                {isArchived(activeConversation) ? "Вернуть из архива" : "Переместить в архив"}
              </button>
            </div>
          </>
        ) : (
          <div className="workspace-list-state">Диалог не выбран.</div>
        )}
      </aside>
    </div>
  );
}
