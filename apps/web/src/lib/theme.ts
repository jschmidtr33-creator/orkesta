import { useEffect, useState } from "react";

/** Tema de la app: claro (predeterminado), oscuro o el del sistema. Se recuerda en este
 * navegador. Las propuestas públicas se ven siempre en claro. */
export type Theme = "light" | "dark" | "system";
const KEY = "ork-theme";
const media = () => window.matchMedia?.("(prefers-color-scheme: dark)");

export function storedTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme: Theme = storedTheme()) {
  const isPublic = window.location.pathname.startsWith("/app/p/");
  const dark = !isPublic && (theme === "dark" || (theme === "system" && Boolean(media()?.matches)));
  document.documentElement.classList.toggle("dark", dark);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0A0E18" : "#080B12");
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(storedTheme);
  useEffect(() => {
    applyTheme(theme);
    if (theme !== "system") return;
    const m = media();
    const onChange = () => applyTheme("system");
    m?.addEventListener?.("change", onChange);
    return () => m?.removeEventListener?.("change", onChange);
  }, [theme]);
  return [
    theme,
    (t: Theme) => {
      try {
        localStorage.setItem(KEY, t);
      } catch {
        /* sin almacenamiento: el tema dura esta sesión */
      }
      setTheme(t);
    },
  ];
}
