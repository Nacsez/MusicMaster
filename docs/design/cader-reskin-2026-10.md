# CADER appearance for Music Mastering Tools

Date: 2026-10-02. Status: implemented; automated palette/asset contracts,
populated browser workflows, responsive/zoom reflow and forced-color presentation
verified. Physical workstation acceptance remains separate.

The owner requested a starker interface that shares CADER's color scheme.
This revision uses CADER's dark palette, solid near-black surfaces, compact
rectangular controls and clearer marked edges. Master, Library and Activity
retain the workflow and responsive layout established by the
[September redesign](ui-ux-redesign-2026-09.md).

## Evidence and provenance

The owned CADER workspace supplied these references:

- `prototype/scripts/ui/cad_palette.gd`: the live palette and display-role
  mapping. SHA-256:
  `429650d3689acadf5797f827ae982ae07cd9bc290e5c407ce4b8d1e2c99ddb9f`.
- `docs/VISIBILITY_AND_PRODUCTIVITY_V0.2.md`: semantic green/pink/purple roles,
  solid backing surfaces, non-color state cues, and contrast targets of 4.5:1
  for readable text and 3:1 for meaningful graphical cues.
- `docs/ICON_UI_V0.3.2.md`: compact controls with descriptive labels,
  contrasting pressed controls, a separate pink focus indicator, and marked
  active edges.
- `prototype/artifacts/icon_ui_tests/1080x720-dark-icons.png`: inspected as a
  visual reference for the solid, compact workbench treatment.

CyberPacker's retained
`docs/reviews/ribbon-identity-2026-09-06/cader-palette-source.gd` snapshot and
`cader-source.json` manifest independently identify the same palette hash.
These are provenance references to adjacent owned projects; runtime assets do
not depend on those projects being installed.

## Token and role contract

The single token source remains the `:root` block in
[`app.css`](../../src/music_mastering_tools/web_assets/app.css).

| CADER role | Value | Mastering Tools use |
| --- | --- | --- |
| Background | `#070709` | Application canvas and header |
| Panel | `#101014` | Working sections, drawers and dialogs |
| Alternate panel | `#1b1b23` | Queue/table headings and secondary controls |
| Field | `#09090d` | Inputs, code/log views and queue backgrounds |
| Hover | `#30313a` | Strong interaction surface |
| Border | `#727684` | Input/control outlines and shell edge |
| Axis | `#4b4e5a` | Decorative separators and panel boundaries |
| Main text | `#f5f7fa` | Titles, values and controls |
| Muted text | `#b6bbc6` | Labels and supporting descriptions |
| Active geometry | `#76ff53` | Active workspace, mix queue, ready states and Render |
| Construction | `#c9a0ff` | Reference queue and reference summaries |
| Dimension/annotation | `#ff78d4` | Keyboard focus |
| Accent text | `#070709` | Text on the green primary fill |

Music-specific extensions are explicit: warning amber `#ffcc66`, error rose
`#ff8b99`, accent hover `#a0ff87` and small-detail gray `#9ba0ad`. The last is
slightly brighter than CADER's disabled gray so detail text remains above
4.5:1 on the darkest-to-lightest interaction surfaces. Semantic fills use
9% alpha over the underlying solid surface. Warning and error states retain
their words and symbols; purple references cannot imply a warning.

## Presentation decisions

1. Use 2 px corners for controls, panels, dialogs and status labels. The
   waveform brand mark and progress bars use square edges. Circular status
   markers remain recognizable alongside their labels.
2. Use solid canvas, header and audition-dock backgrounds. Remove decorative
   gradients and backdrop blur so panel separation comes from value and lines.
3. Show the active workspace with green text, a quiet fill and its existing
   underline. Preserve semantic navigation state and visible keyboard focus.
4. Mark the mix and reference queue headers with green and purple edges,
   respectively, together with their `IN` and `REF` labels. The execution rail
   gets a green top edge to make the primary action location easy to find.
5. Keep keyboard focus pink so focus and action readiness remain separate
   cues. Input focus uses a pink border and keyboard outline; pointer hover
   uses a brighter neutral border.
6. Preserve local packaged assets, accessible names, scroll ownership,
   reduced-motion behavior, Windows forced-color support and all request-bearing
   control IDs. This revision does not alter music processing or persisted
   job, track, run or folder identity.

## Verification and reproduction

The existing packaged-asset suite now contains 15 passing tests. Its added
palette regression checks the shared CADER values, independent semantic roles,
browser theme metadata, square geometry, and absence of gradients/blur. It
also calculates contrast before rounding:

- All main, muted, small-detail, reference, warning and error text tokens meet
  4.5:1 against each of the six opaque application surfaces.
- Control outlines meet 3:1 against their default canvas/panel/input surfaces.
- Text on the green primary fill meets 4.5:1; pink focus meets 3:1 against the
  lightest opaque application surface.

The numeric matrix covers token pairings. It is supplemented by screenshots
and workflow smoke because disabled opacity, native audio controls, display
scaling and real assistive technology need their own evidence.

```powershell
.venv\Scripts\python.exe -m pytest tests/test_portal_assets.py -q
.venv\Scripts\python.exe -m ruff check tests/test_portal_assets.py
.venv\Scripts\python.exe -m ruff format --check tests/test_portal_assets.py
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Invoke-PortalSmoke.ps1 `
  -OutputDirectory artifacts/cader-reskin-desktop -ViewportWidth 1440 -ViewportHeight 900
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Invoke-PortalSmoke.ps1 `
  -OutputDirectory artifacts/cader-reskin-compact -ViewportWidth 800 -ViewportHeight 900
```

Both isolated Chromium runs passed. The screenshots were inspected: the
desktop Master view keeps its execution rail beside the form, the compact
view stacks the workflow, primary navigation remains visible, and reference
table overflow remains inside its queue. Each run retains redacted DOM,
browser/server logs, readiness and verification files and shuts down cleanly:

- `artifacts/cader-reskin-desktop/20261002T215926024Z/`
- `artifacts/cader-reskin-compact/20261002T215926024Z/`

## Populated browser qualification

The reproducible source entrypoint is
[`Invoke-PopulatedPortalSmoke.ps1`](../../scripts/Invoke-PopulatedPortalSmoke.ps1).
It uses the normal developer environment, installed Chrome/Edge and Node.js
22+ with its built-in WebSocket; it adds no application runtime dependency.
Fixtures and browser profiles are restricted to `artifacts/ux-populated-smoke`
and the normal private workspace is never used. The fixture builder creates
three mixes, three references and three completed version lineages, including
two versions of one source. Synthetic one-second WAV outputs are interface
fixtures; this suite does not certify mastering quality.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Invoke-PopulatedPortalSmoke.ps1
```

The visual qualification run at `artifacts/ux-populated-smoke/20261002T221418898Z/` passed
**36 checks** with zero JavaScript runtime exceptions, zero browser page error
logs, zero retained launch-token leaks and graceful portal shutdown. It retains
asset hashes, structured checks and layout metrics, redacted DOM and logs,
screenshots and an explicit visual-review record. Screenshot inspection covered
the populated Library, zoomed Master/Activity and Windows forced-color
presentation.

After browser-session lifecycle integration and diagnostic credential redaction,
the populated regression at `artifacts/ux-populated-smoke/20261002T232015677Z/`
again passed all **36 checks**, with zero runtime exceptions, browser error logs
or retained launch-token leaks and graceful shutdown. The serialized audit JSON,
including exception evidence, and caught error stacks are redacted before writing.

The clean GitHub runner subsequently exposed an audit setup defect: the focus
check used only programmatic `focus()` and sampled computed styles immediately,
without establishing keyboard entry, document focus or a painted frame. Its
normal-color snapshot reported the foreground color for the outline while the
remaining 35 checks passed. The old evidence cannot distinguish browser paint
timing from page-focus state. The audit now sends an actual Shift+Tab from the
following control into the output field, establishes browser focus, awaits two
animation frames and allows at most three seconds for the exact expected style.
The pink outline, green action fill, 3 px width and visible keyboard focus remain
strict requirements; a timeout preserves the last actual snapshot and fails.
Browser version, document/pseudo-class focus, palette token and settling time are
retained for diagnosis. Local regression
`artifacts/ux-populated-smoke/20261002T235325173Z/` passed all **36 checks**;
normal and forced-color snapshots settled in 18 ms and 33 ms, respectively.

The checks exercise direct cross-track A/B candidate choices, keyboard swap
with retained media/playhead, safe blank-slot clearing, nested source/version
keyboard controls across rerenders, dialog Enter submission, Master queue
ordering, bounded Activity/toast overflow and completed batch task
reconstruction after a fresh page load. Library source/detail panes and version
actions remain side by side at 1440 and 1280 px.

Primary Master, Library and Activity controls are checked at 1280 × 800 and
its **200% zoom equivalent**: a 640 × 400 CSS viewport with device scale 2,
producing a 1280 × 800 physical screenshot. Each control is scrolled into the
available area above the fixed comparison dock, then checked for complete
viewport containment and obstruction at its center and all four inset corners.
All workspaces have zero document-level horizontal overflow. This establishes
zoom reflow and action reachability; it is not an OS DPI or native browser
toolbar certification.

This audit found and corrected two presentation defects:

- Chromium forced-color text backplates obscured labels on active Highlight
  fills. Those controls now explicitly use the user's Highlight/HighlightText
  colors with a scoped `forced-color-adjust: none`; the rest of the page keeps
  normal forced-color adaptation. Focus remains visible and the Ready, Warning
  and Error labels retain outlined shapes even when their hues become identical.
- When Library source actions wrapped at 1280 px, the More menu's right-aligned
  popover extended 16 px outside its scrolling detail pane. The More menu now
  stays at the right edge of its action row. Full control hit tests protect this
  case at both standard and zoomed widths.

Physical screen-reader operation, user-selected browser zoom, playback through
the operator's actual codecs/audio device and real Windows file-picker operation
retain their own acceptance requirements. Automated synthetic browser checks do
not establish listening quality or complete accessibility conformance.
