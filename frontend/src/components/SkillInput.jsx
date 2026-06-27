import { useEffect, useRef, useState } from "react";
import { Blocks } from "lucide-react";
import { get } from "../api.js";

/**
 * A textarea that supports an explicit **skill mention** via a ``/`` slash command.
 *
 * Typing ``/`` (at the start or after whitespace) opens a typeahead of the enabled
 * skills; the text after the slash filters them. Arrow keys move the selection,
 * Enter/Tab inserts the chosen skill as a ``/skill-name`` mention (you can add several),
 * and Escape closes the menu. This is the *explicit* way to call a skill — the agent
 * still picks skills automatically from their descriptions, but a ``/mention`` tells it
 * to apply that skill on purpose. (``@`` is reserved for personas, added later.)
 *
 * It is a drop-in for a plain ``<textarea>``: it forwards ``value``/``onChange`` and
 * delegates key events to ``onKeyDown`` whenever the menu is closed (so the parent's
 * Enter-to-send still works). Skills are fetched once and cached across instances.
 */
let _skillCache = null;

function fetchSkills() {
  if (_skillCache) return Promise.resolve(_skillCache);
  return get("/api/capabilities")
    .then((caps) => {
      _skillCache = (caps || [])
        .filter((c) => c.kind === "skill" && c.enabled && c.status === "valid")
        .map((c) => ({ name: c.name, description: c.description || "" }));
      return _skillCache;
    })
    .catch(() => []);
}

/** Find an active ``/token`` ending at the caret (slash at start or after whitespace). */
function activeSlash(value, caret) {
  let i = caret;
  while (i > 0 && /[\w-]/.test(value[i - 1])) i--;
  if (i > 0 && value[i - 1] === "/") {
    const slashPos = i - 1;
    if (slashPos === 0 || /\s/.test(value[slashPos - 1])) {
      return { start: slashPos, query: value.slice(i, caret) };
    }
  }
  return null;
}

export default function SkillInput({
  value, onChange, onKeyDown, placeholder, rows = 1, className = "", autoGrow,
}) {
  const ref = useRef(null);
  const [skills, setSkills] = useState([]);
  const [menu, setMenu] = useState(null);     // { start } when open, else null
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const caretToSet = useRef(null);
  const queryRef = useRef("");                 // last query, to avoid resetting selection

  useEffect(() => { fetchSkills().then(setSkills); }, []);

  // Apply a pending caret position after a programmatic value change.
  useEffect(() => {
    if (caretToSet.current != null && ref.current) {
      const pos = caretToSet.current;
      caretToSet.current = null;
      ref.current.focus();
      try { ref.current.setSelectionRange(pos, pos); } catch { /* noop */ }
    }
  });

  const matches = menu
    ? skills.filter((s) => {
        const q = query.toLowerCase();
        return !q || s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
      }).slice(0, 8)
    : [];

  function refresh(el) {
    const found = activeSlash(el.value, el.selectionStart);
    if (found) {
      setMenu({ start: found.start });
      // Only reset the highlighted option when the search text actually changes, so
      // arrow-key navigation (which also fires keyup) doesn't snap back to the top.
      if (queryRef.current !== found.query) setActive(0);
      queryRef.current = found.query;
      setQuery(found.query);
    } else {
      setMenu(null);
      queryRef.current = "";
    }
  }

  function handleChange(e) {
    onChange(e.target.value);
    if (autoGrow) autoGrow(e.target);
    refresh(e.target);
  }

  function pick(skill) {
    if (!menu) return;
    const el = ref.current;
    const caret = el ? el.selectionStart : value.length;
    const before = value.slice(0, menu.start);
    const after = value.slice(caret);
    const insert = `/${skill.name} `;
    const next = before + insert + after;
    caretToSet.current = (before + insert).length;
    onChange(next);
    setMenu(null);
    if (autoGrow && el) requestAnimationFrame(() => autoGrow(el));
  }

  function handleKeyDown(e) {
    if (menu && matches.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % matches.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a - 1 + matches.length) % matches.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(matches[active]); return; }
      if (e.key === "Escape") { e.preventDefault(); setMenu(null); return; }
    }
    onKeyDown && onKeyDown(e);
  }

  return (
    <div className="skill-input">
      <textarea
        ref={ref} value={value} rows={rows} placeholder={placeholder} className={className}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onKeyUp={(e) => refresh(e.target)}
        onClick={(e) => refresh(e.target)}
        onBlur={() => setTimeout(() => setMenu(null), 120)}
      />
      {menu && matches.length > 0 && (
        <div className="skill-menu" role="listbox">
          <div className="skill-menu-head">Skills · ↑↓ to choose, Enter to insert</div>
          {matches.map((s, i) => (
            <button type="button" key={s.name} role="option" aria-selected={i === active}
              className={"skill-opt" + (i === active ? " active" : "")}
              onMouseDown={(e) => { e.preventDefault(); pick(s); }}
              onMouseEnter={() => setActive(i)}>
              <Blocks size={14} className="skill-opt-ico" />
              <span className="skill-opt-name">{s.name}</span>
              {s.description && <span className="skill-opt-desc">{s.description}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
