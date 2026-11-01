import Image from "@tiptap/extension-image";
import TextAlign from "@tiptap/extension-text-align";
import { Color, TextStyle } from "@tiptap/extension-text-style";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Bold,
  Code2,
  ImagePlus,
  Italic,
  Link2,
  List,
  ListOrdered,
  Loader2,
  Underline,
  Unlink,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../lib/api";
import { cx } from "./ui";

/** Texto plano antiguo → HTML equivalente (párrafos, saltos y enlaces). */
export function toHtml(value: string): string {
  if (!value) return "";
  if (/<(p|div|br|img|a|table|strong|b|em|ul|ol|span|h[1-4])\b/i.test(value)) return value;
  const esc = value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc
    .trim()
    .split(/\n{2,}/)
    .map((p) => `<p>${p.replace(/\n/g, "<br>").replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1">$1</a>')}</p>`)
    .join("");
}

export async function uploadImage(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const res = await api<{ url: string }>("/api/email-assets", { method: "POST", body: form });
  return res.url;
}

type Props = {
  value: string;
  onChange: (html: string) => void;
  onFocus?: (editor: Editor) => void;
  placeholder?: string;
  minHeight?: number;
  allowSource?: boolean;
};

/** Editor de correo: formato, enlaces con texto, listas, alineación, imágenes y vista HTML. */
export function RichEditor({ value, onChange, onFocus, minHeight = 180, allowSource = true }: Props) {
  const [source, setSource] = useState(false);
  const [panel, setPanel] = useState<null | "link" | "image">(null);
  const [url, setUrl] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ link: { openOnClick: false, autolink: true, defaultProtocol: "https" }, heading: { levels: [2, 3] } }),
      Image.configure({ inline: false }),
      TextAlign.configure({ types: ["heading", "paragraph", "image"] }),
      TextStyle,
      Color,
    ],
    content: toHtml(value),
    editorProps: { attributes: { class: "ork-prose focus:outline-none", style: `min-height:${minHeight}px` } },
    onUpdate: ({ editor: e }) => onChange(e.isEmpty ? "" : e.getHTML()),
    onFocus: ({ editor: e }) => onFocus?.(e),
    immediatelyRender: true,
  });

  useEffect(() => {
    if (!editor || editor.isFocused) return;
    const next = toHtml(value);
    if (next !== editor.getHTML()) editor.commands.setContent(next, { emitUpdate: false });
  }, [value, editor]);

  if (!editor) return null;

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      const src = await uploadImage(file);
      editor.chain().focus().setImage({ src, alt: file.name.replace(/\.[^.]+$/, "") }).run();
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudo subir la imagen.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const applyLink = () => {
    const href = url.trim();
    if (!href) {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
    } else {
      const full = /^(https?:|mailto:|tel:)/.test(href) ? href : `https://${href}`;
      if (editor.state.selection.empty && !editor.isActive("link")) {
        editor.chain().focus().insertContent(`<a href="${full}">${full}</a>`).run();
      } else {
        editor.chain().focus().extendMarkRange("link").setLink({ href: full }).run();
      }
    }
    setPanel(null);
    setUrl("");
  };

  const imageSelected = editor.isActive("image");

  return (
    <div className="rounded-lg border border-ork-line focus-within:border-ork-blue/50">
      <div className="flex flex-wrap items-center gap-0.5 border-b border-ork-line bg-ork-surface/60 px-1.5 py-1">
        {!source && (
          <>
            <Tool label="Negrita" active={editor.isActive("bold")} onClick={() => editor.chain().focus().toggleBold().run()}>
              <Bold className="size-4" />
            </Tool>
            <Tool label="Cursiva" active={editor.isActive("italic")} onClick={() => editor.chain().focus().toggleItalic().run()}>
              <Italic className="size-4" />
            </Tool>
            <Tool label="Subrayado" active={editor.isActive("underline")} onClick={() => editor.chain().focus().toggleUnderline().run()}>
              <Underline className="size-4" />
            </Tool>
            <label className="flex size-8 cursor-pointer items-center justify-center rounded-md hover:bg-ork-ink/5" title="Color del texto">
              <span className="size-4 rounded-full border border-ork-line" style={{ background: editor.getAttributes("textStyle").color ?? "#1f2937" }} />
              <input
                type="color"
                className="sr-only"
                aria-label="Color del texto"
                onChange={(e) => editor.chain().focus().setColor(e.target.value).run()}
              />
            </label>
            <Sep />
            <Tool label="Lista" active={editor.isActive("bulletList")} onClick={() => editor.chain().focus().toggleBulletList().run()}>
              <List className="size-4" />
            </Tool>
            <Tool label="Lista numerada" active={editor.isActive("orderedList")} onClick={() => editor.chain().focus().toggleOrderedList().run()}>
              <ListOrdered className="size-4" />
            </Tool>
            <Tool label="Alinear a la izquierda" active={editor.isActive({ textAlign: "left" })} onClick={() => editor.chain().focus().setTextAlign("left").run()}>
              <AlignLeft className="size-4" />
            </Tool>
            <Tool label="Centrar" active={editor.isActive({ textAlign: "center" })} onClick={() => editor.chain().focus().setTextAlign("center").run()}>
              <AlignCenter className="size-4" />
            </Tool>
            <Tool label="Alinear a la derecha" active={editor.isActive({ textAlign: "right" })} onClick={() => editor.chain().focus().setTextAlign("right").run()}>
              <AlignRight className="size-4" />
            </Tool>
            <Sep />
            <Tool
              label="Insertar enlace"
              active={editor.isActive("link") || panel === "link"}
              onClick={() => {
                setUrl(editor.getAttributes("link").href ?? "");
                setPanel(panel === "link" ? null : "link");
              }}
            >
              <Link2 className="size-4" />
            </Tool>
            {editor.isActive("link") && (
              <Tool label="Quitar enlace" onClick={() => editor.chain().focus().extendMarkRange("link").unsetLink().run()}>
                <Unlink className="size-4" />
              </Tool>
            )}
            <Tool label="Insertar imagen" active={panel === "image"} onClick={() => setPanel(panel === "image" ? null : "image")}>
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <ImagePlus className="size-4" />}
            </Tool>
            {imageSelected && (
              <span className="ml-1 flex items-center gap-0.5 text-[12px]">
                {[
                  ["S", "150"],
                  ["M", "300"],
                  ["L", "450"],
                  ["Máx.", "600"],
                ].map(([label, w]) => (
                  <button
                    key={w}
                    type="button"
                    onClick={() => editor.chain().focus().updateAttributes("image", { width: w }).run()}
                    className="rounded-md px-1.5 py-1 font-semibold text-ork-graphite hover:bg-ork-ink/5"
                  >
                    {label}
                  </button>
                ))}
              </span>
            )}
          </>
        )}
        {allowSource && (
          <button
            type="button"
            onClick={() => setSource((s) => !s)}
            className={cx(
              "ml-auto flex items-center gap-1 rounded-md px-2 py-1 text-[12px] font-semibold",
              source ? "bg-ork-blue text-white" : "text-ork-graphite hover:bg-ork-ink/5",
            )}
            title="Ver o pegar el código HTML"
          >
            <Code2 className="size-3.5" /> HTML
          </button>
        )}
      </div>
      {panel === "link" && !source && (
        <div className="flex gap-2 border-b border-ork-line px-2 py-2">
          <input
            autoFocus
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyLink();
              }
            }}
            placeholder="https://calendly.com/… (selecciona antes el texto del enlace)"
            className="ork-focus h-8 flex-1 rounded-md border border-ork-line px-2 text-[13px]"
          />
          <button type="button" onClick={applyLink} className="rounded-md bg-ork-blue px-3 text-[12.5px] font-semibold text-white">
            Aplicar
          </button>
        </div>
      )}
      {panel === "image" && !source && (
        <div className="flex flex-wrap items-center gap-2 border-b border-ork-line px-2 py-2">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="rounded-md bg-ork-blue px-3 py-1.5 text-[12.5px] font-semibold text-white"
          >
            Subir desde mi computador
          </button>
          <span className="text-[12px] text-ork-mist">o pega la URL:</span>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim()) {
                e.preventDefault();
                editor.chain().focus().setImage({ src: url.trim() }).run();
                setUrl("");
                setPanel(null);
              }
            }}
            placeholder="https://…/banner.png"
            className="ork-focus h-8 min-w-[180px] flex-1 rounded-md border border-ork-line px-2 text-[13px]"
          />
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
        </div>
      )}
      {error && <p className="px-3 pt-2 text-[12.5px] text-bad">{error}</p>}
      {source ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          className="block w-full resize-y rounded-b-lg px-3 py-2 font-mono text-[12.5px] leading-relaxed text-ork-graphite focus:outline-none"
          style={{ minHeight }}
          placeholder="<p>Pega aquí tu HTML…</p>"
        />
      ) : (
        <div
          className="px-3 py-2"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            const file = e.dataTransfer.files?.[0];
            if (file?.type.startsWith("image/")) {
              e.preventDefault();
              upload(file);
            }
          }}
          onPaste={(e) => {
            const file = Array.from(e.clipboardData.files).find((f) => f.type.startsWith("image/"));
            if (file) {
              e.preventDefault();
              upload(file);
            }
          }}
        >
          <EditorContent editor={editor} />
        </div>
      )}
    </div>
  );
}

function Tool({ label, active, onClick, children }: { label: string; active?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={active}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={cx("flex size-8 items-center justify-center rounded-md", active ? "bg-ork-blue/10 text-ork-blue" : "text-ork-graphite hover:bg-ork-ink/5")}
    >
      {children}
    </button>
  );
}

function Sep() {
  return <span className="mx-1 h-5 w-px bg-ork-line" aria-hidden />;
}

/** Vista del HTML tal como se verá en el correo (sin scripts). */
export function HtmlFrame({ html, minHeight = 120 }: { html: string; minHeight?: number }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(minHeight);
  const doc = `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:0;padding:12px 16px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5;color:#1f2937}img{max-width:100%;height:auto}p{margin:0 0 12px}a{color:#1a56db}</style></head><body>${html}</body></html>`;
  return (
    <iframe
      ref={ref}
      title="Vista previa del correo"
      sandbox="allow-same-origin"
      srcDoc={doc}
      onLoad={() => {
        const body = ref.current?.contentDocument?.body;
        if (body) setHeight(Math.max(minHeight, body.scrollHeight + 8));
        ref.current?.contentDocument?.querySelectorAll("img").forEach((img) =>
          img.addEventListener("load", () => setHeight(Math.max(minHeight, (ref.current?.contentDocument?.body.scrollHeight ?? 0) + 8))),
        );
      }}
      className="w-full border-0"
      style={{ height }}
    />
  );
}
