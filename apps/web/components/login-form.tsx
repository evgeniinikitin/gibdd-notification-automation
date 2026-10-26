"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Icon } from "@/components/icons";
import { api, ApiError, saveTokens } from "@/lib/api";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setNotice(null);

    try {
      const tokens = await api.login(email.trim(), password);
      saveTokens(tokens, remember);
      const currentUser = await api.me();
      if (currentUser.role === "admin") {
        router.replace("/admin");
        return;
      }
      const onboarding = await api.onboarding();
      router.replace(onboarding.completed ? "/" : "/onboarding");
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : "Не удалось выполнить вход");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-context">
        <div className="login-brand">
          <span className="brand-mark brand-mark-light">B</span>
          <span>Buscript</span>
        </div>

        <div className="login-context-content">
          <span className="context-kicker">
            <span className="status-dot" />
            Рабочая платформа
          </span>
          <h1>Уведомления.<br />Без ручной рутины.</h1>
          <p>Подготовка, проверка и тестовая подача документов в одной рабочей среде.</p>
        </div>

        <div className="login-system-status">
          <div>
            <span className="system-pulse" />
            <span>
              <strong>Защищённый вход</strong>
              <small>Доступ по электронной почте и паролю</small>
            </span>
          </div>
        </div>
      </section>

      <section className="login-panel">
        <div className="login-form-wrap">
          <div className="login-heading">
            <span className="mobile-login-brand">
              <span className="brand-mark">B</span>
              Buscript
            </span>
            <span className="eyebrow">Личный кабинет</span>
            <h2>Вход в аккаунт</h2>
            <p>Введите электронную почту и пароль, указанные при регистрации.</p>
          </div>

          <form className="auth-form" onSubmit={handleSubmit}>
            <label className="field-group">
              <span>Электронная почта</span>
              <span className="input-shell">
                <input
                  autoComplete="email"
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="name@example.test"
                  required
                  type="email"
                  value={email}
                />
              </span>
            </label>

            <label className="field-group">
              <span>Пароль</span>
              <span className="input-shell input-with-action">
                <input
                  autoComplete="current-password"
                  minLength={8}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Введите пароль"
                  required
                  type={showPassword ? "text" : "password"}
                  value={password}
                />
                <button
                  aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                  onClick={() => setShowPassword((value) => !value)}
                  type="button"
                >
                  <Icon name="eye" size={18} />
                </button>
              </span>
            </label>

            <div className="form-options">
              <label className="check-control">
                <input
                  checked={remember}
                  onChange={(event) => setRemember(event.target.checked)}
                  type="checkbox"
                />
                <span>
                  <Icon name="check" size={13} />
                </span>
                Запомнить меня
              </label>
              <Link className="text-button" href="/register">Создать аккаунт</Link>
            </div>

            {notice && (
              <div className="inline-notice auth-error" role="alert">
                <Icon name="alert" size={17} />
                {notice}
              </div>
            )}

            <button className="button button-dark button-full auth-submit" disabled={loading} type="submit">
              <span>{loading ? "Подключаем…" : "Войти"}</span>
              <span className="button-icon-circle">
                {loading ? <span className="spinner" /> : <Icon name="arrow" size={16} />}
              </span>
            </button>
          </form>

          <div className="login-help">
            <Icon name="profile" size={17} />
            <span>
              <strong>Нет аккаунта?</strong>
              <small><Link href="/register">Зарегистрировать компанию</Link></small>
            </span>
          </div>
        </div>
      </section>
    </main>
  );
}
