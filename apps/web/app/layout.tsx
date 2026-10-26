import type { Metadata, Viewport } from "next";
import "@/app/globals.css";

export const metadata: Metadata = {
  title: {
    default: "Buscript",
    template: "%s · Buscript",
  },
  description: "Рабочая платформа автоматизации уведомлений о перевозке детей",
};

export const viewport: Viewport = {
  colorScheme: "light",
  themeColor: "#F5F5F3",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
