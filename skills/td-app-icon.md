---
name: td-app-icon
description: Create or rebrand compact SVG identity cards for TouchDesigner Base COMPs through the td-http-api bridge. Use when an app needs a branded operator viewer that follows the td-http-api card layout.
---

# TouchDesigner app icon card

Use this skill to create a small, readable SVG card for a TouchDesigner app or Base COMP. Keep the layout consistent while adapting the logo, product name, version, app label, port, and accent color.

## Layout

- Use a landscape canvas matching the target Base COMP's node aspect ratio. The `td-http-api` card uses `viewBox="0 0 320 260"` (16:13), matching its 160 × 130 network node, and renders at 640 × 520.
- Keep the hierarchy: replaceable logo in the upper area; product name with version immediately after it; separate `App:` and `Port:` rows near the bottom.
- Use a near-black background, quiet dark row surfaces, warm white primary text, muted row labels, and one configurable accent color. The current palette is in `_VIEWER_COLORS` in `src/td_http_api.py`.
- Keep row labels and values on a shared baseline. Leave a visible gap between rows and comfortable outer padding, especially under the last row.
- Keep the tool name and version distinct from the dynamic app label. If they happen to be identical, retain the `App:` field because it identifies the running instance.

## Build or adapt the card

1. Read `/init`, then `/docs?name=td-http-api` for bridge conventions. Inspect `/network` to identify the target component and its `opviewer`; use a path-specific network request before changing anything.
2. Check the actual target node's width and height. Set the SVG `viewBox` and renderer output resolution to the same ratio; do not assume every component is 16:13.
3. If the target uses this bridge's existing renderer, change `_VIEWER_PRODUCT` and `_VIEWER_COLORS`, replace the `text_logo_svg` mark, and keep app label, port, and version values dynamic. Otherwise, create a self-contained SVG with the same hierarchy and assign it to the target's existing SVG/Web Render path. Inspect operator names and parameters with `/opinfo` and TouchDesigner details with `/help` before relying on them.
4. When wrapping a logo's inner SVG shapes, preserve its root `fill` attribute. This lets open strokes such as chevrons stay unfilled while shapes with their own explicit fill keep their color. Escape dynamic text for XML and clip or ellipsize long app labels so they stay within the row.
5. Render the operator viewer and inspect it with `/snapshot`. Check the full card at its real aspect ratio for clipping, alignment, readable text, logo contrast, row spacing, and bottom padding. Adjust and capture another snapshot if needed.

## TouchDesigner changes

Make live network changes only when requested. Prefer the bridge's `/par`, `/dat`, `/build`, and other dedicated routes; use `/run` only when the operation cannot be expressed through them. Each mutation is an undo step. Follow the user's instructions about saving or exporting the project; do not overwrite existing `.toe` or `.tox` files as an incidental part of icon work.

For a derivative of the `td-http-api` component, include this skill in its `docs_skill_td_app_icon` Text DAT. The bridge's `/docs?name=td-app-icon` endpoint can then deliver the same layout guidance to an agent working in another project.
