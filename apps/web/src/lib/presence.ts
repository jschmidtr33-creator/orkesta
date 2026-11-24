import { useEffect } from "react";
import { api } from "./api";

/** Suma un minuto mientras ORKESTA está abierto y visible, y si hubo interacción en él.
 * Solo la jefatura y la administración ven este tiempo (Mi equipo). */
export function usePresence(enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let last = Date.now();
    const touch = () => {
      last = Date.now();
    };
    const events = ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const ping = () => {
      if (document.visibilityState !== "visible") return;
      const path = window.location.pathname.replace(/^\/app/, "");
      api("/api/reports/presence", { method: "POST", json: { path, active: Date.now() - last < 60_000 } }).catch(() => undefined);
    };
    ping();
    const timer = window.setInterval(ping, 60_000);
    return () => {
      window.clearInterval(timer);
      events.forEach((e) => window.removeEventListener(e, touch));
    };
  }, [enabled]);
}
