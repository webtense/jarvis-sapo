/**
 * JARVIS — Admin screen, DOM vocabulary.
 *
 * THE RULE OF THIS FOLDER: nothing is ever assembled as a string of HTML.
 * This page paints the names of the user's own Home Assistant entities, the
 * names of their MCP servers and the lines of an audit log — three files
 * JARVIS does not write and has no say over. No API that parses markup is
 * used anywhere in `src/admin/` (a test greps for the two usual ones), and
 * every helper below ends in `textContent`.
 */

/** An element with classes and, optionally, text. Text goes in as TEXT. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/** A panel: a titled box. The returned body is what callers fill. */
export function panel(title: string, meta = ""): { root: HTMLElement; body: HTMLElement } {
  const root = el("section", "panel");
  const head = el("header", "panel-head");
  head.appendChild(el("h2", "panel-title", title));
  if (meta) head.appendChild(el("span", "panel-meta", meta));
  root.appendChild(head);
  const body = el("div", "panel-body");
  root.appendChild(body);
  return { root, body };
}

/** A label/value line, for things that are read and not edited. */
export function readout(label: string, value: string, tone = ""): HTMLElement {
  const row = el("div", `readout${tone ? ` ${tone}` : ""}`);
  row.appendChild(el("span", "readout-label", label));
  row.appendChild(el("span", "readout-value", value || "—"));
  return row;
}

/** A paragraph of explanation under a control. */
export function note(text: string): HTMLElement {
  return el("p", "note", text);
}

/** A labelled form control, wired for the screen reader and for the pointer:
 * the label's `for` and the control's `id` are the same string. */
export function field(id: string, label: string, control: HTMLElement): HTMLElement {
  const wrap = el("div", "field");
  const tag = el("label", "field-label", label);
  tag.htmlFor = id;
  control.id = id;
  wrap.appendChild(tag);
  wrap.appendChild(control);
  return wrap;
}

/** A <select> built from (value, label) pairs. */
export function select(options: Array<[string, string]>, current: string): HTMLSelectElement {
  const node = el("select", "control");
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    node.appendChild(option);
  }
  node.value = options.some(([v]) => v === current) ? current : (options[0]?.[0] ?? "");
  return node;
}

/** A number input with the server's own bounds on it. The bounds are a
 * courtesy to the user, never the check: the server refuses out of range. */
export function numberInput(value: string, min: number, max: number): HTMLInputElement {
  const node = el("input", "control");
  node.type = "number";
  node.inputMode = "numeric";
  node.min = String(min);
  node.max = String(max);
  node.step = "1";
  node.value = value;
  return node;
}

export function textInput(value: string, maxChars: number): HTMLInputElement {
  const node = el("input", "control");
  node.type = "text";
  node.maxLength = maxChars;
  node.value = value;
  return node;
}

export function button(text: string, kind = ""): HTMLButtonElement {
  const node = el("button", `btn${kind ? ` ${kind}` : ""}`, text);
  node.type = "button";
  return node;
}

/** A row of buttons plus the line where the result of pressing one lands. */
export function actions(...nodes: HTMLElement[]): { root: HTMLElement; result: HTMLElement } {
  const root = el("div", "actions");
  for (const node of nodes) root.appendChild(node);
  const result = el("p", "result");
  result.hidden = true;
  const wrap = el("div", "actions-wrap");
  wrap.appendChild(root);
  wrap.appendChild(result);
  return { root: wrap, result };
}

export function say(node: HTMLElement, text: string, tone = ""): void {
  node.textContent = text;
  node.className = `result${tone ? ` ${tone}` : ""}`;
  node.hidden = !text;
}

/** A list of lines of text that came out of a file. */
export function lines(items: string[], empty: string): HTMLElement {
  const list = el("ul", "lines");
  if (items.length === 0) {
    list.appendChild(el("li", "lines-empty", empty));
    return list;
  }
  for (const item of items) list.appendChild(el("li", "", item));
  return list;
}
