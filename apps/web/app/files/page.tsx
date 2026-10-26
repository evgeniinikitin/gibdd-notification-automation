import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { FilesView } from "@/components/files-view";

export const metadata: Metadata = {
  title: "Файлы",
};

export default function FilesPage() {
  return (
    <AppShell>
      <FilesView />
    </AppShell>
  );
}
