"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/icons";
import { ProgressBar, StatusBadge } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import type { FileEntry, Trip } from "@/lib/types";

type ArchiveFile = FileEntry & {
  tripId?: string;
};

type DownloadResult = {
  blob: Blob;
  filename: string;
};

type RequestsApi = typeof api & {
  downloadFile(id: string): Promise<DownloadResult>;
};

const requestsApi = api as RequestsApi;

const filters = [
  { id: "all", label: "Все" },
  { id: "active", label: "В работе" },
  { id: "review", label: "Нужно внимание" },
  { id: "complete", label: "Поданные" },
];

function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Требуется авторизация. Войдите в аккаунт, чтобы увидеть поездки.";
    }
    return error.message;
  }
  return "Не удалось загрузить данные из API.";
}

function filterTrips(trips: Trip[], filter: string, query: string) {
  const normalized = query.trim().toLowerCase();
  return trips.filter((trip) => {
    const matchesQuery =
      !normalized ||
      trip.reference.toLowerCase().includes(normalized) ||
      trip.customer.toLowerCase().includes(normalized) ||
      trip.route.toLowerCase().includes(normalized);
    if (!matchesQuery) return false;
    if (filter === "active") {
      return !["submitted", "completed", "needs_support"].includes(trip.status);
    }
    if (filter === "review") {
      return ["clarifying", "needs_support"].includes(trip.status);
    }
    if (filter === "complete") {
      return ["submitted", "completed"].includes(trip.status);
    }
    return true;
  });
}

function belongsToTrip(file: ArchiveFile, trip: Trip) {
  if (file.tripId) return file.tripId === trip.id;
  if (!file.tripReference) return false;
  return file.tripReference === trip.reference;
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

export function RequestsView() {
  const [trips, setTrips] = useState<Trip[]>([]);
  const [files, setFiles] = useState<ArchiveFile[]>([]);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.allSettled([api.trips(), api.files()])
      .then(([tripsResult, filesResult]) => {
        if (cancelled) return;
        const errors: string[] = [];
        if (tripsResult.status === "fulfilled") {
          setTrips(tripsResult.value);
        } else {
          setTrips([]);
          errors.push(getErrorMessage(tripsResult.reason));
        }
        if (filesResult.status === "fulfilled") {
          setFiles(filesResult.value as ArchiveFile[]);
        } else {
          setFiles([]);
          errors.push(getErrorMessage(filesResult.reason));
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

  const visibleTrips = useMemo(() => filterTrips(trips, filter, query), [filter, query, trips]);
  async function downloadFile(file: ArchiveFile) {
    setActionError(null);
    try {
      saveDownload(await requestsApi.downloadFile(file.id));
    } catch (error) {
      setActionError(getErrorMessage(error));
    }
  }

  return (
    <div className="page-view">
      <header className="page-header">
        <div>
          <span className="eyebrow">Данные из вашего аккаунта</span>
          <h1>Поездки</h1>
          <p>Статусы обработки и сформированные документы по поданным заявкам.</p>
        </div>
        <Link className="button button-dark" href="/">
          Новая поездка
          <span className="button-icon-circle">
            <Icon name="plus" size={15} />
          </span>
        </Link>
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

      <section className="data-region">
        <div className="data-toolbar">
          <div className="filter-tabs" role="tablist" aria-label="Фильтр поездок">
            {filters.map((item) => (
              <button
                aria-selected={filter === item.id}
                className={filter === item.id ? "is-active" : ""}
                key={item.id}
                onClick={() => setFilter(item.id)}
                role="tab"
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>
          <label className="search-field">
            <Icon name="search" size={17} />
            <input
              aria-label="Поиск поездок"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Номер, заказчик или маршрут"
              value={query}
            />
          </label>
        </div>

        <div className="data-table-wrap">
          <table className="data-table requests-table">
            <thead>
              <tr>
                <th>Поездка</th>
                <th>Заказчик / маршрут</th>
                <th>Дата поездки</th>
                <th>Пассажиры</th>
                <th>Статус</th>
                <th>Прогресс</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visibleTrips.map((trip) => {
                const isOpen = selectedTripId === trip.id;
                const tripFiles = files.filter((file) => belongsToTrip(file, trip));
                return (
                  <Fragment key={trip.id}>
                    <tr className={isOpen ? "is-open" : ""}>
                      <td>
                        <button
                          className="table-primary request-open-button"
                          onClick={() => setSelectedTripId(isOpen ? null : trip.id)}
                          type="button"
                        >
                          {trip.reference}
                        </button>
                        <small>{trip.updatedAt}</small>
                      </td>
                      <td>
                        <strong>{trip.customer}</strong>
                        <small>{trip.route}</small>
                      </td>
                      <td>
                        <span className="inline-icon-label">
                          <Icon name="calendar" size={15} />
                          {trip.date}
                        </span>
                      </td>
                      <td>
                        <span className="inline-icon-label">
                          <Icon name="users" size={15} />
                          {trip.childrenCount}
                        </span>
                      </td>
                      <td>
                        <StatusBadge status={trip.status} />
                      </td>
                      <td>
                        <div className="table-progress">
                          <ProgressBar value={trip.progress} size="compact" />
                          <small>{trip.progress}%</small>
                        </div>
                      </td>
                      <td>
                        <button
                          aria-expanded={isOpen}
                          className="button button-secondary button-small"
                          onClick={() => setSelectedTripId(isOpen ? null : trip.id)}
                          type="button"
                        >
                          {isOpen ? "Закрыть" : "Подробнее"}
                        </button>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="request-detail-row">
                        <td colSpan={7}>
                          <div className="request-detail">
                            <dl>
                              <div>
                                <dt>Заказчик</dt>
                                <dd>{trip.customer}</dd>
                              </div>
                              <div>
                                <dt>Дата и время</dt>
                                <dd>{trip.date}</dd>
                              </div>
                              <div>
                                <dt>Маршрут</dt>
                                <dd>{trip.route}</dd>
                              </div>
                              <div>
                                <dt>Пассажиры</dt>
                                <dd>{trip.childrenCount}</dd>
                              </div>
                            </dl>
                            <section>
                              <div className="request-detail-heading">
                                <div>
                                  <span>Документы</span>
                                  <strong>{tripFiles.length}</strong>
                                </div>
                                <small>Только автоматически сформированные уведомления и квитанции.</small>
                              </div>
                              {tripFiles.length > 0 ? (
                                <div className="request-document-list">
                                  {tripFiles.map((file) => (
                                    <button
                                      key={file.id}
                                      onClick={() => void downloadFile(file)}
                                      type="button"
                                    >
                                      <Icon name="document" size={18} />
                                      <span>
                                        <strong>{file.name}</strong>
                                        <small>{file.updatedAt}</small>
                                      </span>
                                      <Icon name="download" size={17} />
                                    </button>
                                  ))}
                                </div>
                              ) : (
                                <p className="request-no-documents">
                                  Сформированных документов по этой поездке пока нет.
                                </p>
                              )}
                            </section>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>

          {loading && (
            <div className="table-empty">
              <Icon name="refresh" size={24} />
              <strong>Загружаем поездки…</strong>
            </div>
          )}
          {!loading && visibleTrips.length === 0 && (
            <div className="table-empty">
              <Icon name={query || filter !== "all" ? "search" : "route"} size={24} />
              <strong>
                {query || filter !== "all" ? "Поездки не найдены" : "Поездок пока нет"}
              </strong>
              <span>
                {query || filter !== "all"
                  ? "Измените запрос или выберите другой статус."
                  : "Созданные из диалогов поездки появятся здесь автоматически."}
              </span>
              {!query && filter === "all" && (
                <Link className="button button-dark button-small" href="/">
                  Создать поездку
                </Link>
              )}
            </div>
          )}
        </div>

        {!loading && trips.length > 0 && (
          <footer className="table-footer">
            <span>
              Показано {visibleTrips.length} из {trips.length}
            </span>
          </footer>
        )}
      </section>
    </div>
  );
}
