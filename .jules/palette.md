# Palette's Journal

## 2025-05-18 - High-contrast focus rings and summary bounds in minimal CSS templates
**Learning:** In minimalist self-contained HTML/CSS templates using native `<details>` and `<summary>`, default browser focus rings on `summary` stretch across the full width of block containers creating awkward rectangular outlines, while custom styled buttons (`button.primary`) can swallow default browser focus rings on colored backgrounds. Constraining `<summary>` with `width: fit-content` alongside dedicated `:focus-visible` styles with `outline-offset` provides clean, discoverable keyboard accessibility without adding component libraries.
**Action:** When working on native minimal CSS forms, always pair `width: fit-content` on summary toggles and assign `:focus-visible` using the theme's `--accent` token and 2px offset for buttons, links, and disclosure controls.
