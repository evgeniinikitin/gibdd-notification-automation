"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Icon } from "@/components/icons";
import { api, ApiError, saveTokens } from "@/lib/api";

export function RegisterForm() {
  const router = useRouter();
  const [companyName, setCompanyName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice(null);
    if (password !== confirmation) {
      setNotice("Пароли не совпадают");
      return;
    }
    setLoading(true);
    try {
      const tokens = await api.register({
        companyName: companyName.trim(),
        fullName: fullName.trim(),
        email: email.trim(),
        password,
      });
      saveTokens(tokens, true);
      router.replace("/onboarding");
    } catch (error) {
      setNotice(error instanceof ApiError ? error.message : "Не удалось создать аккаунт");
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
            Новый рабочий кабинет
          </span>
          <h1>Одна компания.<br />Один профиль.</h1>
          <p>
            Зарегистрируйте организацию, заполните данные перевозчика и начните создавать
            уведомления в изолированной рабочей области.
          </p>
        </div>

        <div className="login-system-status">
          <div>
            <span className="system-pulse" />
            <span>
              <strong>Данные сохраняются в Buscript</strong>
              <small>После регистрации откроется настройка профиля</small>
            </span>
          </div>
        </div>
      </section>

      <section className="login-panel register-panel">
        <div className="login-form-wrap register-form-wrap">
          <div className="login-heading">
            <span className="mobile-login-brand">
              <span className="brand-mark">B</span>
              Buscript
            </span>
            <span className="eyebrow">Регистрация</span>
            <h2>Создать аккаунт</h2>
            <p>Все поля относятся к вашей реальной организации и учётной записи.</p>
          </div>

          <form className="auth-form" onSubmit={handleSubmit}>
            <label className="field-group">
              <span>Название компании</span>
              <span className="input-shell">
                <input
                  autoComplete="organization"
                  maxLength={255}
                  minLength={2}
                  onChange={(event) => setCompanyName(event.target.value)}
                  placeholder="ООО «Название»"
                  required
                  value={companyName}
                />
              </span>
            </label>

            <label className="field-group">
              <span>Ваше имя</span>
              <span className="input-shell">
                <input
                  autoComplete="name"
                  maxLength={255}
                  minLength={2}
                  onChange={(event) => setFullName(event.target.value)}
                  placeholder="Имя и фамилия"
                  required
                  value={fullName}
                />
              </span>
            </label>

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
                  autoComplete="new-password"
                  minLength={8}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder="Не менее 8 символов"
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

            <label className="field-group">
              <span>Повторите пароль</span>
              <span className="input-shell">
                <input
                  autoComplete="new-password"
                  minLength={8}
                  onChange={(event) => setConfirmation(event.target.value)}
                  required
                  type={showPassword ? "text" : "password"}
                  value={confirmation}
                />
              </span>
            </label>

            {notice && (
              <div className="inline-notice auth-error" role="alert">
                <Icon name="alert" size={17} />
                {notice}
              </div>
            )}

            <button className="button button-dark button-full auth-submit" disabled={loading} type="submit">
              <span>{loading ? "Создаём аккаунт…" : "Зарегистрироваться"}</span>
              <span className="button-icon-circle">
                {loading ? <span className="spinner" /> : <Icon name="arrow" size={16} />}
              </span>
            </button>
          </form>

          <div className="login-help">
            <Icon name="lock" size={17} />
            <span>
              <strong>Уже зарегистрированы?</strong>
              <small><Link href="/login">Войти в аккаунт</Link></small>
            </span>
          </div>
        </div>
      </section>
    </main>
  );
}
