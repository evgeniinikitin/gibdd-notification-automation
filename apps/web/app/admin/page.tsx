import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { AdminOverview } from "@/components/admin-overview";

export const metadata: Metadata = {
  title: "Администрирование",
};

export default function AdminPage() {
  return (
    <AppShell>
      <AdminOverview />
    </AppShell>
  );
}
