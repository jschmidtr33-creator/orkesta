import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { useState } from "react";
import { api } from "../lib/api";
import { Alert, Button, Modal, SelectField, TextField } from "./ui";

type Result = {
  received: number;
  created: number;
  existing: number;
  from_index: number;
  with_email: number;
  no_company: number;
  invalid: string[];
  errors: string[];
  enrichment_id: string | null;
  enrichment_busy: boolean;
};
type Options = { countries: { value: string; label: string }[] };
type ListItem = { id: string; name: string };

/** Agregar contactos pegando direcciones de perfiles: ORKESTA no abre esa red; usa su índice y
 * el motor de enriquecimiento. */
export function LinkedInImportButton({ variant = "secondary" }: { variant?: "secondary" | "ghost" }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant={variant} icon={<Link2 className="size-4" aria-hidden />} onClick={() => setOpen(true)}>
        Agregar desde LinkedIn
      </Button>
      <LinkedInImportModal open={open} onOpenChange={setOpen} />
    </>
  );
}

export function LinkedInImportModal({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const client = useQueryClient();
  const options = useQuery({ queryKey: ["research-options"], queryFn: () => api<Options>("/api/research/options"), staleTime: Infinity, enabled: open });
  const lists = useQuery({ queryKey: ["lists"], queryFn: () => api<ListItem[]>("/api/lists"), enabled: open });
  const [urls, setUrls] = useState("");
  const [company, setCompany] = useState("");
  const [country, setCountry] = useState("");
  const [listId, setListId] = useState("");
  const add = useMutation({
    mutationFn: () =>
      api<Result>("/api/contacts/linkedin", {
        method: "POST",
        json: { urls, company, country, list_id: listId || null, enrich: true },
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["contacts"] });
      client.invalidateQueries({ queryKey: ["lists"] });
    },
  });
  const r = add.data;
  const count = urls.split(/[\s,;]+/).filter((u) => /linkedin\.com\/in\//i.test(u)).length;
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) {
      add.reset();
      setUrls("");
    }
  };
  return (
    <Modal
      open={open}
      onOpenChange={close}
      title="Agregar desde LinkedIn"
      description="Pega direcciones de perfiles (una por línea, hasta 200). ORKESTA no entra a LinkedIn: si ya conoce a la persona trae sus datos; si no, toma el nombre de la dirección y busca su correo con el enriquecimiento."
      wide
    >
      {!r ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <label className="text-[13px] font-semibold text-ork-ink">
            Direcciones de perfiles
            <textarea
              value={urls}
              onChange={(e) => setUrls(e.target.value)}
              rows={7}
              placeholder={"https://www.linkedin.com/in/carlos-ramirez\nhttps://www.linkedin.com/in/maria-torres-4b2a91"}
              className="ork-focus mt-1 w-full rounded-xl border border-ork-line bg-ork-card p-3 font-mono text-[12.5px] text-ork-ink"
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-3">
            <TextField label="Empresa (si todos son de la misma)" value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Ej.: Bancolombia" />
            <SelectField label="País" value={country} onChange={(e) => setCountry(e.target.value)}>
              <option value="">Detectar</option>
              {(options.data?.countries ?? []).map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </SelectField>
            <SelectField label="Guardar también en la lista" value={listId} onChange={(e) => setListId(e.target.value)}>
              <option value="">Solo en Contactos</option>
              {(lists.data ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
            </SelectField>
          </div>
          <p className="text-[12.5px] text-ork-mist">Sin empresa, ORKESTA solo puede buscar el correo de quienes ya están en su índice. Con la empresa (y el país), encuentra su sitio web y arma y verifica el correo.</p>
          {add.error && <Alert>{add.error.message}</Alert>}
          <div className="flex justify-end">
            <Button type="submit" loading={add.isPending} disabled={count === 0}>
              Agregar {count || ""} perfil{count === 1 ? "" : "es"}
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-col gap-3 text-[13.5px] text-ork-graphite">
          <p className="text-[15px] font-semibold text-ork-ink">
            Listo: {r.created} contacto{r.created === 1 ? "" : "s"} nuevo{r.created === 1 ? "" : "s"} de {r.received}.
          </p>
          <ul className="list-disc pl-5">
            <li>{r.from_index} ya estaban en el índice de ORKESTA ({r.with_email} con correo publicado).</li>
            {r.existing > 0 && <li>{r.existing} ya estaban en tus contactos.</li>}
            {r.enrichment_id && <li>El resto se está enriqueciendo: busca su sitio web y su correo. Lo verás en Contactos en unos minutos.</li>}
            {r.enrichment_busy && <li>Hay otro enriquecimiento en curso: cuando termine, usa «Enriquecer» en Contactos para completar estos.</li>}
            {r.no_company > 0 && <li>{r.no_company} sin empresa: agrégala en su ficha para poder buscar su correo.</li>}
            {r.invalid.length > 0 && <li>No son perfiles de LinkedIn: {r.invalid.join(" · ")}</li>}
            {r.errors.length > 0 && <li className="text-warn">{r.errors.join(" · ")}</li>}
          </ul>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => add.reset()}>
              Agregar más
            </Button>
            <Button onClick={() => close(false)}>Listo</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
