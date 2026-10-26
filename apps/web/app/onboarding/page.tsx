import type { Metadata } from "next";
import { Onboarding } from "@/components/onboarding";

export const metadata: Metadata = {
  title: "Настройка компании",
};

export default function OnboardingPage() {
  return <Onboarding />;
}
