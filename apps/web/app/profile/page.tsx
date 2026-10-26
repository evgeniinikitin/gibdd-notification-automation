import type { Metadata } from "next";
import { AppShell } from "@/components/app-shell";
import { ProfileView } from "@/components/profile-view";

export const metadata: Metadata = {
  title: "Профиль компании",
};

export default function ProfilePage() {
  return (
    <AppShell>
      <ProfileView />
    </AppShell>
  );
}
