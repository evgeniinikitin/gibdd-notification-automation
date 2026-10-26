"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { FileGlyph } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { FileEntry, Trip } from "@/lib/types";

type DownloadResult = {
  blob: Blob;
  filename: string;
};

type FilesApi = typeof api & {
  downloadFile(id: string): Promise<DownloadResult>;
};

type ArchiveFolder = {
  id: string;
  label: string;
  description: string;
  files: FileEntry[];
};

const filesApi = api as FilesApi;
const ARCHIVE_KINDS = new Set(["notification", "receipt"]);

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Требуется авторизация. Войдите в аккаунт, чтобы открыть архив уведомлений.";
    }
    return error.message;
  }
  return "Не удалось загрузить архив из API.";
}

function formatSize(size?: number) {
  if (!size) return "—";
  if (size > 1_000_000) return `${(size / 1_000_000).toFixed(1)} МБ`;
  return `${Math.max(1, Math.round(size / 1_000))} КБ`;
}

function artifactLabel(kind?: string) {
  if (kind === "receipt") return "Квитанция";
  return "Уведомление";
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

function buildFolders(files: FileEntry[], trips: Trip[]): ArchiveFolder[] {
  const folders = new Map<string, ArchiveFolder>();
  for (const file of files) {
    const trip = file.tripId
      ? trips.find((item) => item.id === file.tripId)
      : trips.find((item) => file.tripReference === item.reference);
    const id = file.tripId ?? trip?.id ?? file.tripReference ?? "without-trip";
    const current = folders.get(id);
    if (current) {
      current.files.push(file);
      continue;
    }
    folders.set(id, {
      id,
      label: trip?.reference ?? file.tripReference ?? "Без связи с поездкой",
      description: trip?.customer ?? "Системные документы",
      files: [file],
    });
  }
  return [...folders.values()];
}

export function FilesView() {
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [activeFolder, setActiveFolder] = useState("all");
  const [view, setView] = useState<"list" | "grid">("list");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([api.files(), api.trips()])
      .then(([filesResult, tripsResult]) => {
        if (cancelled) return;
        const errors: string[] = [];
        if (filesResult.status === "fulfilled") {
          setFiles(
            filesResult.value.filter(
              (file) => !file.artifactKind || ARCHIVE_KINDS.has(file.artifactKind),
            ),
          );
        } else {
          setFiles([]);
          errors.push(getErrorMessage(filesResult.reason));
        }
        if (tripsResult.status === "fulfilled") {
          setTrips(tripsResult.value);
        } else {
          setTrips([]);
          errors.push(getErrorMessage(tripsResult.reason));
        }
        setLoadErrors([...new Set(errors)]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const folders = useMemo(() => buildFolders(files, trips), [files, trips]);
  const selectedFolder = useMemo(
    () => folders.find((folder) => folder.id === activeFolder),
    [activeFolder, folders],
  );
  const visibleFiles = useMemo(() => {
    const source = activeFolder === "all" ? files : (selectedFolder?.files ?? []);
    const normalized = query.trim().toLowerCase();
    if (!normalized) return source;
    return source.filter(
      (file) =>
        file.name.toLowerCase().includes(normalized) ||
        artifactLabel(file.artifactKind).toLowerCase().includes(normalized) ||
        file.tripReference?.toLowerCase().includes(normalized),
    );
  }, [activeFolder, files, query, selectedFolder]);

  async function downloadFile(file: FileEntry) {
    if (downloadingId) return;
    setDownloadingId(file.id);
    setActionError(null);
    try {
      saveDownload(await filesApi.downloadFile(file.id));
    } catch (error) {
      setActionError(getErrorMessage(error));
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <div className="page-view files-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Автоматический архив</span>
          <h1>Уведомления</h1>
          <p>Папки создаются по поездкам. Здесь доступны только уведомления и квитанции системы.</p>
        </div>
      </header>

      {loadErrors.map((error) => (
        <div className="view-error-banner page-error-banner" key={error} role="alert">
          <Icon name="alert" size={17} />
          <span>{error}</span>
        </div>
      ))}
      {actionError && (
        <div className="view-error-banner page-error-banner" role="alert">
          <Icon name="alert" size={17} />
          <span>{actionError}</span>
          <button aria-label="Закрыть сообщение" onClick={() => setActionError(null)} type="button">
            <Icon name="close" size={14} />
          </button>
        </div>
      )}

      <div className="file-manager">
        <aside className="folder-tree">
          <div className="folder-tree-head">
            <span>Поездки</span>
          </div>
          <nav aria-label="Папки уведомлений">
            <button
              className={activeFolder === "all" ? "is-active" : ""}
              onClick={() => setActiveFolder("all")}
              type="button"
            >
              <Icon name="folder" size={17} />
              <span>Все уведомления</span>
              <small>{files.length}</small>
            </button>
            {folders.map((folder) => (
              <button
                className={activeFolder === folder.id ? "is-active" : ""}
                key={folder.id}
                onClick={() => setActiveFolder(folder.id)}
                title={folder.description}
                type="button"
              >
                <Icon name="folder" size={17} />
                <span className="archive-folder-label">
                  <strong>{folder.label}</strong>
                  <small>{folder.description}</small>
                </span>
                <small>{folder.files.length}</small>
              </button>
            ))}
          </nav>
          <div className="automatic-archive-note">
            <Icon name="lock" size={16} />
            <span>
              <strong>Архив формируется автоматически</strong>
              <small>Документы появляются после генерации уведомления.</small>
            </span>
          </div>
        </aside>

        <section className="file-surface">
          <div className="breadcrumb-line">
            <button onClick={() => setActiveFolder("all")} type="button">
              Архив уведомлений
            </button>
            <Icon name="chevron" size={14} />
            <strong>{selectedFolder?.label ?? "Все уведомления"}</strong>
          </div>
          <div className="file-toolbar">
            <label className="search-field">
              <Icon name="search" size={17} />
              <input
                aria-label="Поиск уведомлений"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Найти документ"
                value={query}
              />
            </label>
            <span className="view-toggle">
              <button
                aria-label="Список"
                className={view === "list" ? "is-active" : ""}
                onClick={() => setView("list")}
                type="button"
              >
                <Icon name="list" size={17} />
              </button>
              <button
                aria-label="Плитка"
                className={view === "grid" ? "is-active" : ""}
                onClick={() => setView("grid")}
                type="button"
              >
                <Icon name="grid" size={17} />
              </button>
            </span>
          </div>

          {loading ? (
            <div className="file-empty-state">
              <Icon name="refresh" size={24} />
              <strong>Загружаем архив…</strong>
            </div>
          ) : visibleFiles.length === 0 ? (
            <div className="file-empty-state">
              <Icon name={query ? "search" : "document"} size={25} />
              <strong>{query ? "Документы не найдены" : "Архив пока пуст"}</strong>
              <span>
                {query
                  ? "Измените поисковый запрос или откройте другую папку."
                  : "Уведомления и квитанции появятся здесь после автоматической генерации."}
              </span>
            </div>
          ) : view === "list" ? (
            <div className="file-table-wrap">
              <table className="data-table file-table">
                <thead>
                  <tr>
                    <th>Документ</th>
                    <th>Тип</th>
                    <th>Поездка</th>
                    <th>Создан</th>
                    <th>Размер</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {visibleFiles.map((file) => (
                    <tr key={file.id}>
                      <td>
                        <button
                          className="file-name-cell"
                          disabled={downloadingId === file.id}
                          onClick={() => void downloadFile(file)}
                          type="button"
                        >
                          <FileGlyph kind={file.kind} />
                          <span>
                            <strong>{file.name}</strong>
                            <small>
                              {downloadingId === file.id ? "Скачивание…" : "Нажмите, чтобы скачать"}
                            </small>
                          </span>
                        </button>
                      </td>
                      <td>
                        <span className="reference-pill">{artifactLabel(file.artifactKind)}</span>
                      </td>
                      <td>{file.tripReference ?? "—"}</td>
                      <td>{file.updatedAt}</td>
                      <td>{formatSize(file.size)}</td>
                      <td>
                        <button
                          aria-label={`Скачать ${file.name}`}
                          className="icon-button icon-button-small"
                          disabled={downloadingId === file.id}
                          onClick={() => void downloadFile(file)}
                          type="button"
                        >
                          <Icon name="download" size={17} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="file-grid">
              {visibleFiles.map((file) => (
                <button
                  disabled={downloadingId === file.id}
                  key={file.id}
                  onClick={() => void downloadFile(file)}
                  type="button"
                >
                  <FileGlyph kind={file.kind} />
                  <span>
                    <strong>{file.name}</strong>
                    <small>
                      {artifactLabel(file.artifactKind)} · {file.updatedAt} · {formatSize(file.size)}
                    </small>
                  </span>
                  <Icon name="download" size={17} />
                </button>
              ))}
            </div>
          )}

          <footer className="file-statusbar">
            <span>{visibleFiles.length} документов</span>
            <span>Только системные уведомления и квитанции</span>
          </footer>
        </section>
      </div>
    </div>
  );
}
