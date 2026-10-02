import type { Page } from "playwright";
import { assertEditable } from "./session.js";

export interface FormControl {
  /** CSS selector that uniquely targets this control; pass it back to fill/upload/click. */
  selector: string;
  tag: string;
  type: string;
  name: string | null;
  id: string | null;
  label: string;
  value: string;
  checked?: boolean;
  options?: { value: string; text: string; selected: boolean }[];
  visible: boolean;
}

export interface ButtonInfo {
  selector: string;
  text: string;
  visible: boolean;
}

/**
 * Lists every form control and button on the current page with a stable selector and a human label.
 * This is how a model learns a Steamworks page without hard-coded selectors that break when Valve redesigns.
 */
export async function inspectPage(page: Page, maxValueLength = 200): Promise<{ url: string; title: string; controls: FormControl[]; buttons: ButtonInfo[] }> {
  assertEditable(page);
  const data = await page.evaluate((maxLen) => {
    const cssEscape = (s: string) => (window as any).CSS.escape(s);
    const selectorFor = (el: Element): string => {
      if (el.id) return `#${cssEscape(el.id)}`;
      const name = el.getAttribute("name");
      if (name) {
        const sel = `${el.tagName.toLowerCase()}[name="${name.replace(/"/g, '\\"')}"]`;
        const all = document.querySelectorAll(sel);
        if (all.length === 1) return sel;
        if ((el as HTMLInputElement).type === "radio" || (el as HTMLInputElement).type === "checkbox") {
          const v = (el as HTMLInputElement).value;
          const withValue = `${sel}[value="${v.replace(/"/g, '\\"')}"]`;
          if (document.querySelectorAll(withValue).length === 1) return withValue;
        }
      }
      const parts: string[] = [];
      let cur: Element | null = el;
      while (cur && cur !== document.body) {
        const parent: Element | null = cur.parentElement;
        const tag = cur.tagName.toLowerCase();
        if (!parent) break;
        const same = Array.from(parent.children).filter((c) => c.tagName === cur!.tagName);
        parts.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(cur) + 1})` : tag);
        if ((parent as HTMLElement).id) {
          parts.unshift(`#${cssEscape((parent as HTMLElement).id)}`);
          break;
        }
        cur = parent;
      }
      return parts.join(" > ");
    };
    const labelFor = (el: Element): string => {
      const id = el.id;
      if (id) {
        const l = document.querySelector(`label[for="${cssEscape(id)}"]`);
        if (l?.textContent?.trim()) return l.textContent.trim();
      }
      const wrapping = el.closest("label");
      if (wrapping?.textContent?.trim()) return wrapping.textContent.trim();
      const aria = el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title");
      if (aria) return aria;
      // Steamworks often renders labels as the previous sibling / table cell.
      let prev: Element | null = el.closest("td, div, p")?.previousElementSibling ?? null;
      for (let i = 0; i < 2 && prev; i++) {
        const t = prev.textContent?.trim();
        if (t) return t.slice(0, 120);
        prev = prev.previousElementSibling;
      }
      return "";
    };
    const isVisible = (el: Element) => {
      const r = (el as HTMLElement).getBoundingClientRect();
      const st = getComputedStyle(el as HTMLElement);
      return r.width > 0 && r.height > 0 && st.visibility !== "hidden" && st.display !== "none";
    };

    const controls = Array.from(document.querySelectorAll("input, textarea, select, [contenteditable='true']"))
      .filter((el) => !["hidden", "submit", "button", "image", "reset"].includes((el as HTMLInputElement).type))
      .map((el) => {
        const tag = el.tagName.toLowerCase();
        const input = el as HTMLInputElement;
        const value = tag === "select" ? (el as HTMLSelectElement).value : el.getAttribute("contenteditable") ? (el.textContent ?? "") : (input.value ?? "");
        return {
          selector: selectorFor(el),
          tag,
          type: el.getAttribute("contenteditable") ? "contenteditable" : input.type || tag,
          name: el.getAttribute("name"),
          id: el.id || null,
          label: labelFor(el).replace(/\s+/g, " ").slice(0, 160),
          value: value.length > maxLen ? value.slice(0, maxLen) + `… (${value.length} chars)` : value,
          ...(input.type === "checkbox" || input.type === "radio" ? { checked: input.checked } : {}),
          ...(tag === "select"
            ? { options: Array.from((el as HTMLSelectElement).options).slice(0, 80).map((o) => ({ value: o.value, text: o.text.trim(), selected: o.selected })) }
            : {}),
          visible: isVisible(el),
        };
      });

    const buttons = Array.from(document.querySelectorAll("button, input[type=submit], input[type=button], a.btn_medium, a[class*='btn'], [role=button]"))
      .map((el) => ({
        selector: selectorFor(el),
        text: ((el as HTMLInputElement).value || el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
        visible: isVisible(el),
      }))
      .filter((b) => b.text !== "");

    return { url: location.href, title: document.title, controls, buttons };
  }, maxValueLength);
  return data;
}

export interface FillChange {
  selector: string;
  before: string;
  after: string;
  applied: boolean;
  error?: string;
}

/** Fills controls by selector. With dryRun, only reports what would change. Never submits the form. */
export async function fillFields(page: Page, fields: { selector: string; value: string | boolean }[], dryRun: boolean): Promise<FillChange[]> {
  assertEditable(page);
  const changes: FillChange[] = [];
  for (const f of fields) {
    const loc = page.locator(f.selector);
    try {
      const count = await loc.count();
      if (count !== 1) throw new Error(count === 0 ? "No element matches this selector." : `${count} elements match; use a more specific selector.`);
      const kind = await loc.evaluate((el) => {
        const t = (el as HTMLInputElement).type;
        if (el.getAttribute("contenteditable") === "true") return "contenteditable";
        if (el.tagName === "SELECT") return "select";
        if (t === "checkbox" || t === "radio") return "check";
        return "text";
      });
      const before = await loc.evaluate((el) =>
        (el as HTMLInputElement).type === "checkbox" || (el as HTMLInputElement).type === "radio"
          ? String((el as HTMLInputElement).checked)
          : el.getAttribute("contenteditable") === "true"
            ? el.textContent ?? ""
            : (el as HTMLInputElement).value ?? "",
      );
      const after = String(f.value);
      if (!dryRun && before !== after) {
        if (kind === "select") await loc.selectOption(after);
        else if (kind === "check") await loc.setChecked(f.value === true || after === "true");
        else await loc.fill(after);
      }
      changes.push({ selector: f.selector, before: truncate(before), after: truncate(after), applied: !dryRun && before !== after });
    } catch (err: any) {
      changes.push({ selector: f.selector, before: "", after: truncate(String(f.value)), applied: false, error: err?.message ?? String(err) });
    }
  }
  return changes;
}

function truncate(s: string, n = 300): string {
  return s.length > n ? `${s.slice(0, n)}… (${s.length} chars)` : s;
}
