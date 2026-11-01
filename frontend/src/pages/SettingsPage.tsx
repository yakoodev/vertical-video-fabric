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

// module — вкладка видна, только пока модуль включён (app/modules.py).
const TABS: { seg: string; label: string; module?: string }[] = [
  { seg: "render", label: "Пресеты рендера" },
  { seg: "banners", label: "Баннеры" },
  { seg: "audio", label: "Музыка" },
  { seg: "subtitles", label: "Субтитры" },
  { seg: "brands", label: "Бренды" },
  { seg: "prompts", label: "Промпты" },
  { seg: "defaults", label: "По умолчанию" },
  { seg: "modules", label: "Модули" },
  { seg: "proxy", label: "Прокси" },
  { seg: "telegram", label: "Telegram", module: "telegram" },
  { seg: "backup", label: "Экспорт/импорт" },
];

export function SettingsPage() {
  const { on } = useModules();
  const telegramOn = on("telegram");
  return (
    <>
      <PageHead title="Настройки" sub="Пресеты рендера, баннеры, музыка, субтитры, бренды, промпты, модули" />
      <div className="settings-layout">
        <nav className="settings-rail">
          {TABS.filter((t) => !t.module || on(t.module)).map((t) => (
            <NavLink
              key={t.seg}
              to={`/settings/${t.seg}`}
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
            <Route path="*" element={<Navigate to="render" replace />} />
          </Routes>
        </div>
      </div>
    </>
  );
}
