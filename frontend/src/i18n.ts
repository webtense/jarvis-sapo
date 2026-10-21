/**
 * JARVIS — textos de la interfaz en dos idiomas.
 *
 * LA REGLA QUE MANDA: con el idioma inglés (el de por defecto) esta capa no
 * cambia ni un byte de lo que la interfaz ya mostraba. El dashboard se prueba
 * con Playwright (tests/test_dashboard_page.py) y esas pruebas buscan las
 * frases en inglés tal cual; por eso `t()` devuelve su argumento intacto en
 * "en" y solo consulta el catálogo en "es".
 *
 * La clave de cada texto ES el texto en inglés: el código sigue leyéndose
 * igual, y un texto sin traducir cae al inglés en vez de romperse o quedar
 * vacío. Las variables van entre llaves: t("{n} docs", { n: 3 }).
 *
 * Idioma, por orden: VITE_JARVIS_LANG (la fija scripts/start-ubuntu.sh a
 * partir de JARVIS_LANGUAGE: "es-ES" o "en-US"), el atributo lang del
 * documento y, por último, "en".
 *
 * Esto traduce solo el MARCO de la interfaz. Lo que viene de un modelo, de una
 * transcripción, de la memoria o del disco son datos y nunca pasan por aquí.
 */
import { ES } from "./i18n-es";

export type Lang = "es" | "en";

type Vars = Record<string, string | number>;

function detect(): Lang {
  let fromEnv = "";
  try {
    fromEnv = String(import.meta.env.VITE_JARVIS_LANG ?? "").toLowerCase();
  } catch {
    fromEnv = "";
  }
  if (fromEnv.startsWith("es")) return "es";
  if (fromEnv.startsWith("en")) return "en";
  const fromDoc = (typeof document !== "undefined"
    ? document.documentElement.lang : "").toLowerCase();
  return fromDoc.startsWith("es") ? "es" : "en";
}

const CURRENT: Lang = detect();

export function lang(): Lang {
  return CURRENT;
}

export function isEs(): boolean {
  return CURRENT === "es";
}

function fill(template: string, vars: Vars): string {
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in vars ? String(vars[name]) : whole);
}

/** El texto en el idioma activo. En inglés es el propio argumento. */
export function t(text: string, vars?: Vars): string {
  const template = CURRENT === "es" ? (ES[text] ?? text) : text;
  return vars ? fill(template, vars) : template;
}

/**
 * Lo mismo con contexto, para cuando dos usos del mismo texto inglés se dicen
 * distinto en castellano ("Failed" de un contador y "Failed" de un botón). La
 * entrada del catálogo es `contexto` + U+0004 + texto; si no existe, vale la
 * general. En inglés es el propio texto.
 */
export function tc(context: string, text: string): string {
  if (CURRENT !== "es") return text;
  return ES[`${context}\u0004${text}`] ?? ES[text] ?? text;
}

/**
 * Plural: `one` cuando n es 1, `other` en el resto (el 0 va en plural, en
 * inglés y en castellano). Las dos formas son claves del catálogo y {n} se
 * rellena solo.
 */
export function tp(n: number, one: string, other: string, vars: Vars = {}): string {
  return t(n === 1 ? one : other, { n, ...vars });
}

// ── Números, fechas y duraciones ────────────────────────────────────────────

/**
 * Argumento de configuración regional para toLocale*String. En inglés es la
 * lista vacía, que es exactamente lo que el código pasaba antes (la del
 * navegador); en castellano, es-ES.
 */
export function loc(): string | string[] {
  return CURRENT === "es" ? "es-ES" : [];
}

/** Entero con separador de miles de la región. */
export function fmtInt(n: number): string {
  return CURRENT === "es" ? n.toLocaleString("es-ES") : n.toLocaleString();
}

/** `toFixed` en inglés; con coma decimal en castellano. */
export function fmtFixed(n: number, digits: number): string {
  if (CURRENT !== "es") return n.toFixed(digits);
  return n.toLocaleString("es-ES", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

/** "5m" en inglés, "5 min" en castellano; s, m, h y d. */
export function unit(value: number | string, u: "s" | "m" | "h" | "d"): string {
  if (CURRENT !== "es") return `${value}${u}`;
  return `${value} ${u === "m" ? "min" : u}`;
}

/** "3m ago" / "hace 3 min". */
export function ago(span: string): string {
  return CURRENT === "es" ? `hace ${span}` : `${span} ago`;
}

// ── Texto fijo del HTML ─────────────────────────────────────────────────────

function normal(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Traduce el primer nodo de texto no vacío del elemento, respetando los
 * espacios que lo rodean. El texto original (normalizado) es la clave. */
function translateFirstText(node: Element): void {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType !== Node.TEXT_NODE) continue;
    const raw = child.textContent ?? "";
    const key = normal(raw);
    if (!key) continue;
    const lead = raw.match(/^\s*/)?.[0] ?? "";
    const trail = raw.match(/\s*$/)?.[0] ?? "";
    child.textContent = lead + t(key) + trail;
    return;
  }
}

/**
 * Traduce lo que está escrito a mano en el HTML. Solo toca elementos marcados
 * con data-i18n (texto), data-i18n-title o data-i18n-aria-label (atributos):
 * nada de lo que el programa construye después pasa por aquí.
 */
export function translateStatic(root: ParentNode = document): void {
  if (CURRENT !== "es") return;
  root.querySelectorAll("[data-i18n]").forEach(translateFirstText);
  for (const attr of ["title", "aria-label"]) {
    root.querySelectorAll(`[data-i18n-${attr}]`).forEach((node) => {
      const current = node.getAttribute(attr);
      if (current) node.setAttribute(attr, t(current));
    });
  }
}

function init(): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = CURRENT === "es" ? "es" : document.documentElement.lang;
  if (CURRENT === "es") document.title = t(document.title);
  translateStatic(document);
}

init();
