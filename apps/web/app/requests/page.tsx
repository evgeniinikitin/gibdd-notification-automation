import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { RequestsView } from "@/components/requests-view";

export const metadata: Metadata = {
  title: "Заявки",
};

export default function RequestsPage() {
  return (
    <AppShell>
      <RequestsView />
    </AppShell>
  );
}
