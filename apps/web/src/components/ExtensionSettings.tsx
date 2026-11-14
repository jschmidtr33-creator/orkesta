import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, KeyRound } from "lucide-react";
import { useState } from "react";
import { adminDownload, api } from "../lib/api";
import { formatWhen } from "../pages/dashboard";
import { Alert, Badge, Button, Card, CardHeader, TextField } from "./ui";

type Token = { id: string; name: string; prefix: string; created_at: string | null; last_used_at: string | null };

const STEPS = [
  "Descarga el .zip con el botón. Es el mismo archivo que se sube a la Chrome Web Store (Paquete → Subir paquete nuevo).",
  "Para probarla en tu Chrome: descomprímelo, abre chrome://extensions, activa «Modo de desarrollador» y toca «Cargar descomprimida» con la carpeta. Si ya tenías una versión, quítala antes.",
  "Fija la extensión en la barra (ícono de pieza de rompecabezas → alfiler).",
  "Ábrela y toca «Iniciar sesión con ORKESTA» → «Conectar». Los contactos se guardan en la empresa en la que estás en la app.",
];

/** Panel de administración → Extensión de Chrome (solo la plataforma): descarga, instalación y
 * claves. Las claves conectan el navegador con la empresa en la que estás en la app. */
export function ExtensionSettings() {
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: ["ext-tokens"], queryFn: () => api<Token[]>("/api/admin/extension/tokens") });
  const info = useQuery({ queryKey: ["ext-info"], queryFn: () => api<{ version: string }>("/api/admin/extension/info") });
  const [name, setName] = useState("Mi Chrome");
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const create = useMutation({
    mutationFn: () => api<Token & { token: string }>("/api/admin/extension/tokens", { method: "POST", json: { name } }),
    onSuccess: (t) => {
      setCreated(t.token);
      setCopied(false);
      client.invalidateQueries({ queryKey: ["ext-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/admin/extension/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ext-tokens"] }),
  });
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <CardHeader
          title={`Extensión «Guardar en ORKESTA»${info.data?.version ? ` · versión ${info.data.version}` : ""}`}
          description="Con la extensión abierta sobre el perfil de una persona (sitio de empresa, CRM, Gmail o LinkedIn), ORKESTA lee nombre, cargo, empresa, ubicación, correo y teléfono, y la persona solo toca «Guardar contacto». Si falta el correo, ORKESTA lo busca y lo verifica."
          action={
            <Button icon={<Download className="size-4" aria-hidden />} onClick={() => adminDownload("/api/admin/extension/download", `orkesta-extension-${info.data?.version ?? ""}.zip`)}>
              Descargar
            </Button>
          }
        />
        <ol className="list-decimal space-y-1.5 pl-5 text-[13.5px] text-ork-graphite">
          {STEPS.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
        <p className="mt-3 text-[12.5px] text-ork-mist">La extensión solo lee la página cuando la persona la abre, y solo el perfil que tiene en pantalla. Nunca recorre listas ni hace nada sola.</p>
      </Card>
      <Card>
        <CardHeader title="Tus claves" description="Cada clave conecta un navegador con tu usuario. Solo sirve para guardar y consultar tus contactos; si pierdes el equipo, revócala." />
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            create.mutate();
          }}
        >
          <div className="min-w-[180px] flex-1">
            <TextField label="Nombre del navegador" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
          </div>
          <Button type="submit" icon={<KeyRound className="size-4" aria-hidden />} loading={create.isPending}>
            Generar clave
          </Button>
        </form>
        {create.error && <div className="mt-3"><Alert>{create.error.message}</Alert></div>}
        {created && (
          <div className="mt-3 rounded-xl border border-ok/30 bg-ok/5 p-3 text-[13px]">
            <p className="font-semibold text-ork-ink">Copia tu clave ahora: no se vuelve a mostrar.</p>
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded-lg bg-ork-card px-2 py-1.5 text-[12px]">{created}</code>
              <Button
                size="sm"
                variant="secondary"
                icon={<Copy className="size-3.5" aria-hidden />}
                onClick={() => {
                  navigator.clipboard.writeText(created);
                  setCopied(true);
                }}
              >
                {copied ? "Copiada" : "Copiar"}
              </Button>
            </div>
          </div>
        )}
        <ul className="mt-4 divide-y divide-ork-line text-[13px]">
          {(tokens.data ?? []).map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-2 py-2">
              <span className="min-w-0">
                <span className="font-semibold text-ork-ink">{t.name}</span> <Badge tone="neutral">{t.prefix}…</Badge>
                <span className="block text-[12px] text-ork-mist">
                  {t.last_used_at ? `Último uso ${formatWhen(t.last_used_at)}` : "Sin usar todavía"}
                </span>
              </span>
              <Button size="sm" variant="ghost" loading={revoke.isPending && revoke.variables === t.id} onClick={() => revoke.mutate(t.id)}>
                Revocar
              </Button>
            </li>
          ))}
          {tokens.data && tokens.data.length === 0 && <li className="py-2 text-ork-mist">Aún no tienes claves.</li>}
        </ul>
      </Card>
    </div>
  );
}
