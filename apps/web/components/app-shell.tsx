"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Icon, type IconName } from "@/components/icons";
import { api, ApiError, clearTokens } from "@/lib/api";
import type { BalanceSummary, OnboardingStatus, User } from "@/lib/types";

const clientNavigation: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Ассистент", icon: "message" },
  { href: "/requests", label: "Заявки", icon: "document" },
  { href: "/files", label: "Уведомления", icon: "folder" },
  { href: "/profile", label: "Настройки профиля", icon: "settings" },
];

const adminNavigation: { href: string; label: string; icon: IconName }[] = [
  { href: "/admin", label: "Администрирование", icon: "admin" },
];

const roleLabels: Record<User["role"], string> = {
  client_admin: "Администратор компании",
  client_user: "Пользователь",
  support_admin: "Поддержка",
  admin: "Администратор Buscript",
};

function initials(value: string) {
  const letters = value
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("");
  return letters.toUpperCase() || "B";
}

function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link aria-label="Buscript — рабочая область" className="brand" href={href}>
      <span className="brand-mark" aria-hidden="true">B</span>
      <span>Buscript</span>
    </Link>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingStatus | null>(null);
  const [balance, setBalance] = useState<BalanceSummary | null>(null);
  const [tripCount, setTripCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSession = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const currentUser = await api.me();
      setUser(currentUser);
      if (currentUser.role === "admin") {
        setOnboarding(null);
        setBalance(null);
        setTripCount(null);
        return;
      }
      const [onboardingResult, balanceResult, tripsResult] = await Promise.allSettled([
        api.onboarding(),
        api.billing(),
        api.trips(),
      ]);
      setOnboarding(onboardingResult.status === "fulfilled" ? onboardingResult.value : null);
      setBalance(balanceResult.status === "fulfilled" ? balanceResult.value : null);
      setTripCount(tripsResult.status === "fulfilled" ? tripsResult.value.length : null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        clearTokens();
        router.replace("/login");
        return;
      }
      setError(caught instanceof ApiError ? caught.message : "Не удалось загрузить аккаунт");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    const initialLoad = window.setTimeout(() => void loadSession(), 0);
    const reload = () => void loadSession();
    window.addEventListener("buscript:account-updated", reload);
    window.addEventListener("buscript:trips-updated", reload);
    return () => {
      window.clearTimeout(initialLoad);
      window.removeEventListener("buscript:account-updated", reload);
      window.removeEventListener("buscript:trips-updated", reload);
    };
  }, [loadSession]);

  useEffect(() => {
    if (user?.role === "admin" && !pathname.startsWith("/admin")) {
      router.replace("/admin");
    }
  }, [pathname, router, user]);

  async function logout() {
    setUserMenuOpen(false);
    try {
      await api.logout();
    } finally {
      router.replace("/login");
    }
  }

  if (loading && !user) {
    return (
      <main className="shell-auth-state" aria-live="polite">
        <span className="brand-mark">B</span>
        <strong>Загружаем Buscript</strong>
        <span className="shell-spinner" />
      </main>
    );
  }

  if (!user) {
    return (
      <main className="shell-auth-state">
        <span className="brand-mark">B</span>
        <strong>Не удалось открыть рабочую область</strong>
        <p>{error ?? "Авторизуйтесь заново."}</p>
        <div className="header-actions">
          <button className="button button-dark" onClick={() => void loadSession()} type="button">
            Повторить
          </button>
          <button
            className="button button-secondary"
            onClick={() => {
              clearTokens();
              router.replace("/login");
            }}
            type="button"
          >
            Войти заново
          </button>
        </div>
      </main>
    );
  }

  if (user.role === "admin" && !pathname.startsWith("/admin")) {
    return (
      <main className="shell-auth-state" aria-live="polite">
        <span className="brand-mark">B</span>
        <strong>Открываем администрирование</strong>
        <span className="shell-spinner" />
      </main>
    );
  }

  const balanceTotal = balance ? balance.available + balance.reserved : 0;
  const balancePercent =
    balance && balanceTotal > 0 ? Math.round((balance.available / balanceTotal) * 100) : 0;
  const isSystemAdmin = user.role === "admin";
  const navigation = isSystemAdmin ? adminNavigation : clientNavigation;

  return (
    <div className="app-frame">
      <header className="mobile-header">
        <Brand href={isSystemAdmin ? "/admin" : "/"} />
        <button
          aria-expanded={mobileOpen}
          aria-label={mobileOpen ? "Закрыть меню" : "Открыть меню"}
          className="icon-button"
          onClick={() => setMobileOpen((value) => !value)}
          type="button"
        >
          <Icon name={mobileOpen ? "close" : "menu"} />
        </button>
      </header>

      {mobileOpen && (
        <button
          aria-label="Закрыть меню"
          className="mobile-scrim"
          onClick={() => setMobileOpen(false)}
          type="button"
        />
      )}

      <aside className={`app-sidebar ${mobileOpen ? "is-open" : ""}`}>
        <div className="sidebar-top">
          <Brand href={isSystemAdmin ? "/admin" : "/"} />
          <Link
            className="company-switcher"
            href={isSystemAdmin ? "/admin" : "/profile"}
            onClick={() => setMobileOpen(false)}
          >
            <span className="company-avatar">{initials(user.organizationName)}</span>
            <span className="company-copy">
              <small>{isSystemAdmin ? "Система" : "Компания"}</small>
              <strong>{user.organizationName}</strong>
            </span>
            <Icon name="chevron" size={16} />
          </Link>
        </div>

        <nav aria-label="Основная навигация" className="primary-nav">
          <span className="nav-label">
            {isSystemAdmin ? "Управление Buscript" : "Рабочая область"}
          </span>
          {navigation.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            return (
              <Link
                className={`nav-link ${active ? "is-active" : ""}`}
                href={item.href}
                key={item.href}
                onClick={() => setMobileOpen(false)}
              >
                <Icon name={item.icon} size={19} />
                <span>{item.label}</span>
                {item.href === "/requests" && tripCount !== null && tripCount > 0 && (
                  <span className="nav-count">{tripCount}</span>
                )}
              </Link>
            );
          })}
        </nav>

        <div className="sidebar-bottom">
          {!isSystemAdmin && onboarding && !onboarding.completed && (
            <Link
              className="onboarding-callout"
              href="/profile"
              onClick={() => setMobileOpen(false)}
            >
              <span className="callout-icon"><Icon name="alert" size={17} /></span>
              <span>
                <strong>Профиль заполнен на {onboarding.completionPercent}%</strong>
                <small>{onboarding.missingFields[0] ?? "Продолжить настройку"}</small>
              </span>
              <Icon name="arrow" size={16} />
            </Link>
          )}

          {balance && (
            <Link className="balance-strip" href="/profile?tab=balance">
              <span>
                <small>Доступно уведомлений</small>
                <strong>{balance.available}</strong>
              </span>
              <span
                className="balance-ring"
                aria-label={`Доступно ${balance.available} уведомлений`}
                style={{ "--balance-percent": `${balancePercent}%` } as React.CSSProperties}
              >
                <span />
              </span>
            </Link>
          )}

          <div className="user-menu-wrap">
            <button
              aria-expanded={userMenuOpen}
              className="user-row"
              onClick={() => setUserMenuOpen((value) => !value)}
              type="button"
            >
              <span className="user-avatar">{initials(user.fullName)}</span>
              <span>
                <strong>{user.fullName}</strong>
                <small>{roleLabels[user.role]}</small>
              </span>
              <Icon name="chevron" size={17} />
            </button>
            {userMenuOpen && (
              <div className="user-menu">
                <Link
                  href={isSystemAdmin ? "/admin" : "/profile"}
                  onClick={() => setUserMenuOpen(false)}
                >
                  <Icon name={isSystemAdmin ? "admin" : "settings"} size={16} />
                  {isSystemAdmin ? "Администрирование" : "Настройки профиля"}
                </Link>
                <button onClick={() => void logout()} type="button">
                  <Icon name="back" size={16} /> Выйти
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      <main className="app-main">{children}</main>

      <nav aria-label="Мобильная навигация" className="mobile-bottom-nav">
        {navigation.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              className={active ? "is-active" : ""}
              href={item.href}
              key={item.href}
              onClick={() => setMobileOpen(false)}
            >
              <Icon name={item.icon} size={20} />
              <span>{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
