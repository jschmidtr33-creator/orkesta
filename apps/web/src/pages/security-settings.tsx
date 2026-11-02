import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bell, Copy, Mail, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, CardHeader, TextField } from "../components/ui";
import { api } from "../lib/api";

export type SecurityProfile = { mfa_enabled: boolean; recovery_left: number; digest_enabled: boolean };

function RecoveryCodes({ codes }: { codes: string[] }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="rounded-2xl border border-warn/30 bg-warn/5 p-4">
      <p className="text-[13.5px] font-semibold text-ork-ink">Guarda estos códigos de respaldo en un lugar seguro</p>
      <p className="mt-0.5 text-[12.5px] text-ork-mist">Cada uno sirve una sola vez si pierdes el celular. No los volveremos a mostrar.</p>
      <ul className="mt-3 grid grid-cols-2 gap-1.5 font-mono text-[13.5px] text-ork-ink sm:grid-cols-4">
        {codes.map((c) => (
          <li key={c} className="rounded-lg bg-ork-card px-2 py-1 text-center">{c}</li>
        ))}
      </ul>
      <Button
        variant="secondary"
        size="sm"
        className="mt-3"
        icon={<Copy className="size-3.5" aria-hidden />}
        onClick={() => navigator.clipboard?.writeText(codes.join("\n")).then(() => setCopied(true))}
      >
        {copied ? "Copiados" : "Copiar códigos"}
      </Button>
    </div>
  );
}

export function TwoFactorCard({ profile }: { profile: SecurityProfile }) {
  const client = useQueryClient();
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const reload = () => client.invalidateQueries({ queryKey: ["profile"] });
  const setup = useMutation({ mutationFn: () => api<{ secret: string; uri: string; qr: string }>("/api/profile/mfa/setup", { method: "POST" }) });
  const enable = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/api/profile/mfa/enable", { method: "POST", json: { code: code.trim() } }),
    onSuccess: (r) => {
      setCodes(r.recovery_codes);
      setCode("");
      setup.reset();
      reload();
    },
  });
  const disable = useMutation({
    mutationFn: () => api("/api/profile/mfa/disable", { method: "POST", json: { code: code.trim() } }),
    onSuccess: () => {
      setCode("");
      setCodes(null);
      reload();
    },
  });
  const regenerate = useMutation({
    mutationFn: () => api<{ recovery_codes: string[] }>("/api/profile/mfa/recovery", { method: "POST", json: { code: code.trim() } }),
    onSuccess: (r) => {
      setCodes(r.recovery_codes);
      setCode("");
      reload();
    },
  });
  const error = setup.error ?? enable.error ?? disable.error ?? regenerate.error;
  return (
    <Card>
      <CardHeader
        title="Doble verificación"
        description="Además de tu contraseña, pide un código de tu app de autenticación (Google Authenticator, Microsoft Authenticator, 1Password…) al ingresar."
        action={profile.mfa_enabled ? <Badge tone="ok">Activa</Badge> : <Badge>Desactivada</Badge>}
      />
      <div className="flex flex-col gap-4">
        {error && <Alert>{error.message}</Alert>}
        {codes && <RecoveryCodes codes={codes} />}
        {!profile.mfa_enabled && !setup.data && (
          <div>
            <Button icon={<ShieldCheck className="size-4" aria-hidden />} loading={setup.isPending} onClick={() => setup.mutate()}>
              Activar doble verificación
            </Button>
          </div>
        )}
        {!profile.mfa_enabled && setup.data && (
          <form
            className="grid gap-4 sm:grid-cols-[180px_1fr]"
            onSubmit={(e) => {
              e.preventDefault();
              enable.mutate();
            }}
          >
            <img src={setup.data.qr} alt="Código QR para tu app de autenticación" className="size-[180px] rounded-xl border border-ork-line bg-white p-2" />
            <div className="flex flex-col gap-3">
              <p className="text-[13.5px] text-ork-graphite">
                1. Escanea el código QR con tu app de autenticación. Si no puedes escanearlo, escribe esta clave:
              </p>
              <code className="break-all rounded-lg bg-ork-ink/5 px-2.5 py-1.5 text-[13px] tracking-wider text-ork-ink">{setup.data.secret}</code>
              <p className="text-[13.5px] text-ork-graphite">2. Escribe el código de 6 dígitos que muestra la app.</p>
              <div className="flex flex-wrap items-end gap-2">
                <TextField label="Código" inputMode="numeric" autoComplete="one-time-code" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} />
                <Button type="submit" loading={enable.isPending} disabled={code.trim().length < 6}>
                  Confirmar y activar
                </Button>
              </div>
            </div>
          </form>
        )}
        {profile.mfa_enabled && (
          <div className="flex flex-col gap-3">
            <p className="text-[13.5px] text-ork-graphite">
              Te quedan <strong className="text-ork-ink">{profile.recovery_left}</strong> códigos de respaldo. Para generar nuevos o desactivar la doble verificación, confirma con un código.
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <TextField label="Código de la app o de respaldo" autoComplete="one-time-code" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} />
              <Button variant="secondary" loading={regenerate.isPending} disabled={code.trim().length < 6} onClick={() => regenerate.mutate()}>
                Nuevos códigos de respaldo
              </Button>
              <Button variant="ghost" loading={disable.isPending} disabled={code.trim().length < 6} onClick={() => disable.mutate()}>
                Desactivar
              </Button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

export function DigestCard({ profile }: { profile: SecurityProfile }) {
  const client = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api("/api/profile/digest", { method: "PUT", json: { enabled } }),
    onSuccess: (data) => client.setQueryData(["profile"], data),
  });
  const test = useMutation({ mutationFn: () => api<{ to: string }>("/api/profile/digest/test", { method: "POST" }) });
  return (
    <Card>
      <CardHeader
        title="Resumen diario por correo"
        description="De lunes a viernes, entre las 7 y las 10 de la mañana: tus tareas de hoy y vencidas, novedades, oportunidades que se enfrían y el pronóstico del mes. Se envía desde tu propio buzón conectado."
      />
      <div className="flex flex-col gap-3">
        {(toggle.error ?? test.error) && <Alert>{(toggle.error ?? test.error)?.message}</Alert>}
        {test.data && <Alert tone="ok">Te enviamos el resumen a {test.data.to}.</Alert>}
        <label className="flex cursor-pointer items-center gap-3 text-[14px] font-semibold text-ork-ink">
          <input
            type="checkbox"
            className="size-4 accent-[var(--color-ork-blue)]"
            checked={profile.digest_enabled}
            disabled={toggle.isPending}
            onChange={(e) => toggle.mutate(e.target.checked)}
          />
          Recibir el resumen diario
        </label>
        <div>
          <Button variant="secondary" size="sm" icon={<Mail className="size-3.5" aria-hidden />} loading={test.isPending} onClick={() => test.mutate()}>
            Enviarme uno de prueba ahora
          </Button>
        </div>
      </div>
    </Card>
  );
}

function urlKey(base64: string) {
  const pad = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function b64(buffer: ArrayBuffer | null) {
  if (!buffer) return "";
  return btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

export function PushCard() {
  const [sub, setSub] = useState<PushSubscription | null>(null);
  const [checked, setChecked] = useState(false);
  useEffect(() => {
    if (!supported) return;
    navigator.serviceWorker
      .getRegistration("/app/")
      .then((reg) => reg?.pushManager.getSubscription() ?? null)
      .then((s) => setSub(s))
      .finally(() => setChecked(true));
  }, []);
  const on = useMutation({
    mutationFn: async () => {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") throw new Error("El navegador bloqueó las notificaciones. Habilítalas en la configuración del sitio y vuelve a intentar.");
      const reg = await navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" });
      await navigator.serviceWorker.ready;
      const { key } = await api<{ key: string }>("/api/push/key");
      const s = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlKey(key) });
      await api("/api/push/subscribe", { method: "POST", json: { endpoint: s.endpoint, p256dh: b64(s.getKey("p256dh")), auth: b64(s.getKey("auth")) } });
      return s;
    },
    onSuccess: setSub,
  });
  const off = useMutation({
    mutationFn: async () => {
      if (!sub) return;
      await api("/api/push/unsubscribe", { method: "POST", json: { endpoint: sub.endpoint } });
      await sub.unsubscribe();
    },
    onSuccess: () => setSub(null),
  });
  const test = useMutation({ mutationFn: () => api<{ sent: number }>("/api/push/test", { method: "POST" }) });
  const error = on.error ?? off.error ?? test.error;
  return (
    <Card>
      <CardHeader
        title="Notificaciones push"
        description="Avisos en este navegador o celular aunque ORKESTA esté cerrado: respuestas a campañas, propuestas firmadas, tareas asignadas y más."
        action={sub ? <Badge tone="ok">Activas aquí</Badge> : undefined}
      />
      <div className="flex flex-col gap-3">
        {!supported && <Alert tone="blue">Este navegador no admite notificaciones push. En iPhone, agrega ORKESTA a la pantalla de inicio primero.</Alert>}
        {error && <Alert>{error.message}</Alert>}
        {test.data && (
          <Alert tone={test.data.sent ? "ok" : "bad"}>{test.data.sent ? "Listo: revisa la notificación de prueba." : "No hay dispositivos activos para enviar la prueba."}</Alert>
        )}
        {supported && checked && (
          <div className="flex flex-wrap gap-2">
            {sub ? (
              <>
                <Button variant="secondary" size="sm" icon={<Bell className="size-3.5" aria-hidden />} loading={test.isPending} onClick={() => test.mutate()}>
                  Enviar notificación de prueba
                </Button>
                <Button variant="ghost" size="sm" loading={off.isPending} onClick={() => off.mutate()}>
                  Desactivar en este dispositivo
                </Button>
              </>
            ) : (
              <Button size="sm" icon={<Bell className="size-3.5" aria-hidden />} loading={on.isPending} onClick={() => on.mutate()}>
                Activar en este dispositivo
              </Button>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
