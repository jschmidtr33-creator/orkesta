import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { TextField } from "./ui";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

type PickedContact = {
  id: string;
  full_name: string;
  email: string | null;
  company: string | null;
  job_title: string | null;
};

/** Elegir uno o varios contactos puntuales para una campaña muy personalizada. */
export function ContactPicker({
  ids,
  onChange,
}: {
  ids: string[];
  onChange: (ids: string[]) => void;
}) {
  const [q, setQ] = useState("");
  const term = useDebounced(q.trim(), 300);
  const found = useQuery({
    queryKey: ["contact-pick", term],
    queryFn: () =>
      api<{ items: PickedContact[] }>(
        `/api/contacts?q=${encodeURIComponent(term)}`,
      ),
    enabled: term.length >= 2,
  });
  const chosen = useQuery({
    queryKey: ["contact-chosen", ids],
    queryFn: () =>
      Promise.all(
        ids
          .slice(0, 50)
          .map((id) =>
            api<PickedContact>(`/api/contacts/${id}`).catch(() => null),
          ),
      ),
    enabled: ids.length > 0,
  });
  const people = (chosen.data ?? []).filter((x): x is PickedContact =>
    Boolean(x),
  );
  return (
    <div className="flex flex-col gap-2">
      {ids.length > 0 && (
        <ul className="flex flex-col gap-1.5" aria-label="Contactos elegidos">
          {(people.length
            ? people
            : ids.map((id) => ({
                id,
                full_name: "Cargando…",
                email: null,
                company: null,
                job_title: null,
              }))
          ).map((x) => (
            <li
              key={x.id}
              className="flex items-center justify-between gap-2 rounded-lg border border-ork-line px-3 py-2 text-[13px]"
            >
              <span className="min-w-0">
                <span className="block truncate font-semibold text-ork-ink">
                  {x.full_name}
                </span>
                <span className="block truncate text-[12px] text-ork-mist">
                  {[x.job_title, x.company, x.email ?? "sin correo"]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </span>
              <button
                type="button"
                className="shrink-0 text-[12px] font-semibold text-bad hover:underline"
                onClick={() => onChange(ids.filter((i) => i !== x.id))}
              >
                Quitar
              </button>
            </li>
          ))}
        </ul>
      )}
      <TextField
        label={ids.length ? "Agregar otro contacto" : "Busca el contacto"}
        placeholder="Nombre, correo o empresa"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {term.length >= 2 && (
        <ul
          className="max-h-56 overflow-y-auto rounded-lg border border-ork-line"
          aria-label="Resultados"
        >
          {found.isLoading && (
            <li className="px-3 py-2 text-[12.5px] text-ork-mist">Buscando…</li>
          )}
          {found.data && found.data.items.length === 0 && (
            <li className="px-3 py-2 text-[12.5px] text-ork-mist">
              No encontré contactos con «{term}».
            </li>
          )}
          {(found.data?.items ?? [])
            .filter((x) => !ids.includes(x.id))
            .slice(0, 8)
            .map((x) => (
              <li key={x.id}>
                <button
                  type="button"
                  disabled={!x.email}
                  onClick={() => {
                    onChange([...ids, x.id]);
                    setQ("");
                  }}
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[13px] hover:bg-ork-blue/5 disabled:opacity-50"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-ork-ink">
                      {x.full_name}
                    </span>
                    <span className="block truncate text-[12px] text-ork-mist">
                      {[
                        x.job_title,
                        x.company,
                        x.email ?? "sin correo: no se le puede escribir",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                  {x.email && (
                    <span className="shrink-0 text-[12px] font-semibold text-ork-blue">
                      Agregar
                    </span>
                  )}
                </button>
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
