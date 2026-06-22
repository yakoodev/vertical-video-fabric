import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { PageHead } from "@/components/ui";
import { RenderPresetsSettings, BannersSettings, AudioSettings, SubtitlesSettings } from "@/pages/settings/AssetSettings";
import { PromptsSettings } from "@/pages/settings/PromptsSettings";
import { DefaultsSettings } from "@/pages/settings/DefaultsSettings";

const TABS = [
  { seg: "render", label: "Пресеты рендера" },
  { seg: "banners", label: "Баннеры" },
  { seg: "audio", label: "Музыка" },
  { seg: "subtitles", label: "Субтитры" },
  { seg: "prompts", label: "Промпты" },
  { seg: "defaults", label: "По умолчанию" },
];

export function SettingsPage() {
  return (
    <>
      <PageHead title="Настройки" sub="Пресеты рендера, баннеры, музыка, субтитры, промпты" />
      <div className="settings-layout">
        <nav className="settings-rail">
          {TABS.map((t) => (
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
            <Route path="prompts" element={<PromptsSettings />} />
            <Route path="defaults" element={<DefaultsSettings />} />
            <Route path="*" element={<Navigate to="render" replace />} />
          </Routes>
        </div>
      </div>
    </>
  );
}
