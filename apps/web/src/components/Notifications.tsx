import * as Menu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CalendarClock, CheckCheck, FileText, Reply } from "lucide-react";
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { cx } from "./ui";

type Item = { id: string; kind: string; title: string; body: string; link: string; read: boolean; created_at: string };

const ICONS: Record<string, typeof Bell> = { task_due: CalendarClock, reply: Reply, proposal: FileText };

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return new Date(iso).toLocaleDateString("es", { day: "numeric", month: "short" });
}

/** Campana con avisos de ORKESTA: tareas vencidas, respuestas y propuestas. */
export function NotificationBell({ dark }: { dark?: boolean }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ items: Item[]; unread: number }>("/api/notifications"),
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
  });
  const read = useMutation({
    mutationFn: (json: { ids?: string[]; all?: boolean }) => api("/api/notifications/read", { method: "POST", json }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["notifications"] }),
  });
  // Aviso del navegador cuando llega algo nuevo y la pestaña está abierta.
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    const items = q.data?.items ?? [];
    if (seen.current === null) {
      seen.current = new Set(items.map((i) => i.id));
      return;
    }
    for (const i of items) {
      if (!seen.current.has(i.id) && !i.read && "Notification" in window && Notification.permission === "granted") {
        try {
          new Notification(i.title, { body: i.body, tag: i.id });
        } catch {
          /* algunos navegadores no permiten avisos */
        }
      }
      seen.current.add(i.id);
    }
  }, [q.data]);
  const unread = q.data?.unread ?? 0;
  const canAsk = "Notification" in window && Notification.permission === "default";
  return (
    <Menu.Root>
      <Menu.Trigger
        className={cx("ork-focus relative rounded-xl p-2.5", dark ? "text-ork-mist hover:bg-white/[0.06] hover:text-white" : "bg-white text-ork-graphite shadow-sm ring-1 ring-ork-line hover:text-ork-ink")}
        aria-label={unread ? `Notificaciones: ${unread} sin leer` : "Notificaciones"}
      >
        <Bell className="size-[18px]" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-bad px-1 text-[11px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={8} className="z-50 w-[min(92vw,380px)] rounded-2xl border border-ork-line bg-white shadow-[var(--shadow-pop)]">
          <div className="flex items-center justify-between border-b border-ork-line px-4 py-3">
            <p className="text-[14.5px] font-bold text-ork-ink">Notificaciones</p>
            {unread > 0 && (
              <button type="button" onClick={() => read.mutate({ all: true })} className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-ork-blue hover:underline">
                <CheckCheck className="size-3.5" /> Marcar todo como leído
              </button>
            )}
          </div>
          {canAsk && (
            <button type="button" onClick={() => Notification.requestPermission()} className="w-full border-b border-ork-line bg-ork-blue/5 px-4 py-2 text-left text-[12.5px] font-semibold text-ork-blue">
              Activar avisos en este navegador
            </button>
          )}
          <ul className="max-h-[60dvh] overflow-y-auto">
            {(q.data?.items ?? []).length === 0 ? (
              <li className="px-4 py-10 text-center text-[13px] text-ork-mist">Sin notificaciones por ahora.</li>
            ) : (
              q.data!.items.map((n) => {
                const Icon = ICONS[n.kind] ?? Bell;
                return (
                  <li key={n.id}>
                    <Menu.Item asChild>
                      <button
                        type="button"
                        onClick={() => {
                          if (!n.read) read.mutate({ ids: [n.id] });
                          if (n.link.startsWith("/app")) navigate(n.link.slice(4) || "/");
                        }}
                        className={cx("flex w-full gap-3 px-4 py-3 text-left outline-none hover:bg-ork-surface data-[highlighted]:bg-ork-surface", !n.read && "bg-ork-blue/[0.04]")}
                      >
                        <span className={cx("mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full", n.kind === "task_due" ? "bg-warn/15 text-warn" : n.kind === "reply" ? "bg-ok/15 text-ok" : "bg-ork-violet/15 text-ork-violet")}>
                          <Icon className="size-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className={cx("block text-[13.5px] leading-snug", n.read ? "text-ork-graphite" : "font-semibold text-ork-ink")}>{n.title}</span>
                          {n.body && <span className="mt-0.5 block truncate text-[12.5px] text-ork-mist">{n.body}</span>}
                          <span className="mt-0.5 block text-[11.5px] text-ork-mist">{ago(n.created_at)}</span>
                        </span>
                        {!n.read && <span className="mt-2 size-2 shrink-0 rounded-full bg-ork-blue" aria-label="Sin leer" />}
                      </button>
                    </Menu.Item>
                  </li>
                );
              })
            )}
          </ul>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
