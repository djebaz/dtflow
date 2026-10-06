---
name: dtflow-design
description: Use this skill to generate well-branded interfaces and assets for DTFlow, a native Qt desktop workspace for AI image generation. Contains essential design guidelines, colors, typography, tokens, and UI kit components for prototyping and production.
user-invocable: true
---

# DTFlow Design System Skill

This skill gives you access to DTFlow's complete design system: a charcoal-and-blue color palette, precise typography specifications, interaction patterns, and a reference UI kit for the Generate workspace.

## Quick start

1. **Read the README.md** in this skill for the comprehensive design guide, color palette, typography rules, spacing system, and interaction language.

2. **Reference the token files:**
   - `tokens/colors.css` — all color values as CSS custom properties
   - `tokens/typography.css` — font families, sizes, and weights
   - `tokens/spacing.css` — margins, padding, radii, and control sizes

3. **View the foundation cards** in the Design System tab to see colors, type, and spacing in context.

4. **Study the UI kits** (`ui_kits/connection|generate|queues|images/index.html`): one per view, composed from the components in `components/`.

## Design principles

- **Image-first layout**: The generated image takes the largest flexible region; controls remain compact nearby
- **Charcoal depth**: Four surface tones (background, surface, input, canvas) create visual hierarchy
- **Blue action language**: One accent blue for primary actions, focus, and selection
- **Text + color**: Status always pairs color with text labels; color alone never conveys meaning
- **Restrained corners**: 5px (inputs), 6px (buttons), 8px (panels)
- **Precise states**: Every control has rest, hover, pressed, focus, and disabled treatments

## When creating with this system

- **For Qt desktop work**: Reference `DESIGN.md` for the complete layout specification, component checklist, and native widget styling guidance. Use token values from `tokens.json` in your QSS and Python theme code.

- **For web/prototype work**: Link `styles.css` to import all tokens as CSS custom properties. Use the color, typography, and spacing tokens directly. Study the Generate UI kit for layout patterns and interaction design.

- **For new surfaces**: Follow the text hierarchy (title, section, body, small). Verify color contrast (4.5:1 minimum for body text). Test keyboard focus, hover, and press states. Keep empty states informative rather than blank.

## Files in this system

- `readme.md` — complete design guide and manifest
- `styles.css` — root stylesheet importing all tokens
- `tokens/` — CSS custom property definitions
- `cards/` — foundation specimen cards
- `ui_kits/` — Connection, Generate, Queues and Images kits
- `components/` — controls, display, shell and layout components
- `DESIGN.md` — original DTFlow design specification (authoritative for Qt implementation)
- `tokens.json` — original DTFlow token definitions
- `dtflow_dark.qss` — original QSS stylesheet

## Resources

- **Original repository**: [djebaz/dtflow](https://github.com/djebaz/dtflow) — Qt source code, full implementation, and active development
- **Design specification**: See `DESIGN.md` for layout rules, component contracts, page implementation checklist, and do's/don'ts
- **Token authority**: `tokens.json` is the runtime source for all values; this system mirrors it for documentation and web use

---

**Use this skill to:**
- Design new workspaces or tabs following DTFlow's interaction language
- Build web prototypes that match DTFlow's visual identity
- Create production Qt widgets styled consistently with the system
- Reference component states and interaction patterns
- Ask questions about design decisions, layout rules, or token usage
