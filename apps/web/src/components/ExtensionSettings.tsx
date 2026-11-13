import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, KeyRound } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { formatWhen } from "../pages/dashboard";
import { Alert, Badge, Button, Card, CardHeader, TextField } from "./ui";

type Token = { id: string; name: string; prefix: string; created_at: string | null; last_used_at: string | null };

const STEPS = [
  "Descarga la extensión con el botón y descomprime el archivo .zip (queda la carpeta «orkesta-extension»).",
  "En Chrome abre chrome://extensions y activa «Modo de desarrollador» (arriba a la derecha).",
  "Toca «Cargar descomprimida» y elige la carpeta «orkesta-extension».",
  "Fija la extensión en la barra (ícono de pieza de rompecabezas → alfiler).",
  "Genera tu clave aquí abajo, ábrela en la extensión y pégala. Listo.",
];

/** Configuración → Extensión de Chrome: descarga, instalación y claves personales. */
export function ExtensionSettings() {
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: ["ext-tokens"], queryFn: () => api<Token[]>("/api/extension/tokens") });
  const [name, setName] = useState("Mi Chrome");
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const create = useMutation({
    mutationFn: () => api<Token & { token: string }>("/api/extension/tokens", { method: "POST", json: { name } }),
    onSuccess: (t) => {
      setCreated(t.token);
      setCopied(false);
      client.invalidateQueries({ queryKey: ["ext-tokens"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`/api/extension/tokens/${id}`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["ext-tokens"] }),
  });
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card>
        <CardHeader
          title="Extensión «Guardar en ORKESTA»"
          description="Guarda contactos desde cualquier página con un clic: sitios de empresas, Gmail, noticias o directorios. Seleccionas el nombre o el cargo, clic derecho → «Guardar en ORKESTA», revisas y guardas. ORKESTA busca su correo y lo verifica. En LinkedIn agrega el perfil solo por su dirección, sin leer la página."
          action={
            <a href="/api/extension/download" className="ork-focus inline-flex h-9 items-center gap-2 rounded-xl bg-gradient-to-r from-[#2563EB] to-[#8B5CF6] px-3.5 text-[13px] font-semibold text-white">
              <Download className="size-4" aria-hidden /> Descargar
            </a>
          }
        />
        <ol className="list-decimal space-y-1.5 pl-5 text-[13.5px] text-ork-graphite">
          {STEPS.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
        <p className="mt-3 text-[12.5px] text-ork-mist">La extensión solo lee la página cuando tú la usas, y solo lo que eliges guardar. Nunca recorre listas ni hace nada sola.</p>
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
