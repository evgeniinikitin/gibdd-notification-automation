import { Icon } from "@/components/icons";
import type { FileEntry, TripStatus } from "@/lib/types";

const statusMap: Record<TripStatus, { label: string; tone: string }> = {
  draft: { label: "Черновик", tone: "neutral" },
  extracting: { label: "Распознавание", tone: "blue" },
  clarifying: { label: "Нужно уточнение", tone: "warning" },
  validated: { label: "Проверено", tone: "success" },
  generating_documents: { label: "Генерация", tone: "blue" },
  dry_run_ready: { label: "Тест готов", tone: "indigo" },
  submitted: { label: "Подано", tone: "success" },
  completed: { label: "Завершено", tone: "success" },
  needs_support: { label: "Нужна поддержка", tone: "danger" },
};

export function StatusBadge({ status }: { status: TripStatus }) {
  const item = statusMap[status];
  return (
    <span className={`status-badge status-${item.tone}`}>
      <span className="status-dot" />
      {item.label}
    </span>
  );
}

export function ProgressBar({
  value,
  size = "default",
}: {
  value: number;
  size?: "default" | "compact";
}) {
  return (
    <div
      aria-label={`Прогресс ${value}%`}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={value}
      className={`progress-track ${size === "compact" ? "is-compact" : ""}`}
      role="progressbar"
    >
      <span className="progress-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  );
}

export function FileGlyph({ kind }: { kind: FileEntry["kind"] | string }) {
  const isFolder = kind === "folder";
  return (
    <span className={`file-glyph file-${kind}`}>
      <Icon name={isFolder ? "folder" : "document"} size={20} />
      {!isFolder && <small>{kind.toUpperCase()}</small>}
    </span>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div aria-label="Загрузка" className="skeleton-stack" role="status">
      {Array.from({ length: lines }, (_, index) => (
        <span className="skeleton-line" key={index} style={{ width: `${92 - index * 13}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon">
        <Icon name="document" size={24} />
      </span>
      <h3>{title}</h3>
      <p>{body}</p>
      {action}
    </div>
  );
}
