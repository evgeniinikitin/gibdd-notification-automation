import Link from "next/link";
import { Icon } from "@/components/icons";

export default function NotFound() {
  return (
    <main className="standalone-state">
      <div className="brand brand-static">
        <span className="brand-mark">B</span>
        <span>Buscript</span>
      </div>
      <span className="standalone-code">404</span>
      <h1>Такого раздела нет</h1>
      <p>Вернитесь в рабочую область или проверьте адрес страницы.</p>
      <Link className="button button-dark" href="/">
        В рабочую область <Icon name="arrow" size={17} />
      </Link>
    </main>
  );
}
