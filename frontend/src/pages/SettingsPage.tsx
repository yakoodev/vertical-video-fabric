import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { PageHead } from "@/components/ui";
import { RenderPresetsSettings, BannersSettings, AudioSettings, SubtitlesSettings } from "@/pages/settings/AssetSettings";
import { PromptsSettings } from "@/pages/settings/PromptsSettings";
import { DefaultsSettings } from "@/pages/settings/DefaultsSettings";
import { BackupSettings } from "@/pages/settings/BackupSettings";
import { ProxySettings } from "@/pages/settings/ProxySettings";
import { BrandsSettings } from "@/pages/settings/BrandsSettings";
import { TelegramSettings } from "@/pages/settings/TelegramSettings";
import { ModulesSettings } from "@/pages/settings/ModulesSettings";
import { useModules } from "@/hooks/useModules";
import { SECTIONS, visibleTabs } from "@/lib/tabs";

export function SettingsPage() {
  const { on } = useModules();
  const telegramOn = on("telegram");
  return (
    <>
      <PageHead title="Настройки" sub="Пресеты рендера, баннеры, музыка, субтитры, бренды, промпты, модули" />
      <div className="settings-layout">
        <nav className="settings-rail">
          {visibleTabs(SECTIONS.settings.tabs, on).map((t) => (
            <NavLink
              key={t.key}
              to={t.to}
              className={({ isActive }) => `settings-tab${isActive ? " active" : ""}`}
            >
              {t.label}
            </NavLink>
          ))}
        </nav>
        <div className="settings-body">
          <Routes>
            <Route index element={<Navigate to="render" replace />} />
            <Route path="render" element={<RenderPresetsSettings />} />
            <Route path="banners" element={<BannersSettings />} />
            <Route path="audio" element={<AudioSettings />} />
            <Route path="subtitles" element={<SubtitlesSettings />} />
            <Route path="brands" element={<BrandsSettings />} />
            <Route path="prompts" element={<PromptsSettings />} />
            <Route path="defaults" element={<DefaultsSettings />} />
            <Route path="modules" element={<ModulesSettings />} />
            <Route path="backup" element={<BackupSettings />} />
            <Route path="proxy" element={<ProxySettings />} />
            <Route
              path="telegram"
              element={telegramOn ? <TelegramSettings /> : <Navigate to="/settings/modules" replace />}
            />
            <Route path="*" element={<Navigate to="/settings/render" replace />} />
          </Routes>
        </div>
      </div>
    </>
  );
}
