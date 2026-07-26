# Frontend Design Skill: Restrained Industrial Dark UI

Use this skill when redesigning or extending the project's frontend interface. The goal is a mature, functional, engineering-oriented UI with controlled contrast, generous spacing, and minimal accent color usage.

## Core Design Rule

Follow the **60-30-10 color rule**:

- **60% dominant neutral background**: charcoal/near-black base surfaces.
- **30% secondary structure**: slate/gray panels, borders, typography, metadata, and cards.
- **10% accent/action color**: amber only for primary actions, focus states, progress highlights, and important interactive emphasis.

Avoid large areas of vivid color. Accent color should guide attention, not decorate the page.

## Color System

Use a restrained industrial palette:

```css
--bg: #101214;
--panel: rgba(22, 24, 27, 0.82);
--surface: rgba(31, 35, 40, 0.72);
--surface-raised: rgba(42, 47, 54, 0.82);
--line: rgba(226, 232, 240, 0.13);
--line-hover: rgba(245, 158, 11, 0.34);

--text: #d9dee7;
--text-bright: #f8fafc;
--muted: #9aa4b2;
--faint: #687282;

--accent: #f59e0b;
--accent-2: #475569;
--accent-3: #64748b;
--accent-soft: rgba(245, 158, 11, 0.12);
--accent-glow: rgba(245, 158, 11, 0.10);

--ok: #22c55e;
--ok-soft: rgba(34, 197, 94, 0.14);
--warn: #fbbf24;
--warn-soft: rgba(251, 191, 36, 0.12);
--danger: #ef4444;
--danger-soft: rgba(239, 68, 68, 0.12);
```

## Layout & Spacing

Use oversized padding to create clean grouping and breathing room:

- Side panels: `44px 34px 30px`
- Right panel: `44px 34px`
- Top/header bar: `40px 48px 32px`
- Composer/footer: `28px 48px 34px`
- Splash/empty card: `64px 72px`
- Base buttons: `12px 24px`
- Status/action controls: `12px 18px`

Keep visual density moderate. Prefer fewer, larger spacing groups over many tight elements.

## Surface Treatment

Panels should feel layered but not flashy:

```css
background: linear-gradient(180deg, rgba(24, 27, 32, 0.92), rgba(16, 18, 21, 0.84));
backdrop-filter: blur(28px) saturate(1.35);
border-color: var(--line);
```

Use subtle radial gradients sparingly. Neutral surfaces should dominate.

## Buttons

Use predictable component naming and behavior:

- `Button-Base`
- `Button-Primary`
- `Button-Danger`
- `Button-Toggle`

Base button style:

```css
button {
  border: 1px solid var(--line);
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(42, 47, 54, 0.92), rgba(31, 35, 40, 0.72));
  color: var(--text-bright);
  padding: 12px 24px;
  font: 650 13px var(--font);
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255,255,255,0.06);
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease, background 0.16s ease;
}

button:hover {
  transform: translateY(-1px);
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.14), rgba(100, 116, 139, 0.16));
  border-color: var(--line-hover);
  box-shadow: 0 8px 20px rgba(245, 158, 11, 0.12), inset 0 1px 0 rgba(255,255,255,0.10);
}
```

Primary actions should use amber gradients, not blue/purple/pink:

```css
button.primary,
.send {
  border-color: rgba(245, 158, 11, 0.48);
  background: linear-gradient(135deg, #b45309, var(--accent));
  color: #111827;
  box-shadow: 0 8px 20px rgba(245, 158, 11, 0.18);
}
```

Danger actions use red only for destructive states.

## Toggle Buttons

Toggle buttons should be obvious but restrained:

- Rounded pill shape
- Neutral inactive state
- Amber/slate active state
- Small status dot at the left
- Glow only on active state and kept subtle

Avoid decorative switch animations unless necessary.

## Typography

Use clean, predictable typography:

- Primary font: `DM Sans`
- Mono font: `IBM Plex Mono`
- Headings use `var(--text-bright)`
- Metadata uses `var(--faint)` or `var(--muted)`
- Labels may be uppercase with increased letter spacing

## Visual Tone

The interface should feel:

- Mature
- Technical
- Calm
- High-contrast enough for long coding sessions
- Sparse with accent color
- Structured and predictable

Avoid:

- Pink/purple-dominant palettes
- Large colorful gradients
- Excessive glow effects
- Cute or playful color combinations
- Context-specific component class naming when a generic component style will do
