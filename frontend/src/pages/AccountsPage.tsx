import { PageHead } from "@/components/ui";
import { AccountsSettings } from "@/pages/settings/AccountsSettings";

export function AccountsPage() {
  return (
    <>
      <PageHead title="Аккаунты" sub="Cookie-сессии YouTube и TikTok для публикации" />
      <AccountsSettings />
    </>
  );
}
