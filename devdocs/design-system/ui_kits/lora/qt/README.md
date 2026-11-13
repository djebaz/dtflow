# Editable Qt Designer LoRA design references

These `.ui` design references translate the owner-reviewed [LoRA layout](../index.html) and
[design contract](../../../lora-ux.md) into **native Qt Widgets** that can be edited with
Qt Designer or inspected with the offline renderer. The forms in this design folder remain **design-only**. The canonical Prepare
form and its reusable rows now live in `src/dtflow/ui/forms/` and are loaded by
the runtime desktop controller.

| Form | Review surface |
| --- | --- |
| `connections_lora.ui` | Three existing workspace regions; compact five-switch group on the right and a Custom LoRAs library collapsed by default in the center |
| `generate_lora.ui` | Current selection and weights in the right inspector, without a duplicate metadata library |
| `src/dtflow/ui/forms/prepare_lora_batch.ui` + `prepare_lora_row.ui` | Runtime Prepare editor with dynamic rows, full-filename Copy, readiness, managed-name preview and explicit Prepare action |
| `lora_picker.ui` | Selected-first, alphabetical visual list and an independent execution-order example |
| `lora_metadata_editor.ui` | One-record metadata editor over Connections; Cancel and Save metadata only |

The exact architecture choices, names, weights, connection status, and counts shown here
are **synthetic design samples**, not claims about the currently connected backend.

## Previewing native Qt forms

Use DTFlow's locked development environment from the repository root:

```powershell
uv run --frozen python tools/ui/preview_lora_design.py --out tmp/lora-design --connections-expanded --compact-prepare
```

The command loads the canonical Prepare form plus the remaining design reference forms with the **shared DTFlow Qt stylesheet**, writes PNGs
under ignored `tmp/lora-design/`, and does not connect to a provider. Use
`--view connections` (or `generate`, `prepare`, `picker`, `metadata`) to
render a single form. `--connections-expanded` captures the same Connections form
with the library content shown. `--compact-prepare` captures the same horizontal
six-column batch editor at 760 px wide.

On Windows, inspect Qt font/DPI behavior separately. The offline screenshots are
synthetic design evidence, **not** runtime behavior or Windows acceptance.

## Preview on pull requests

Use the single [Preview Action](../../../../../.github/workflows/preview-ui.yml). Relevant non-draft PR updates select representative runtime states automatically; `preview:PART` explicitly requests changed views and `preview:ALL` requests all registered states. Every automatic path skips drafts, including label additions. Manual `mode: category`, `category: lora` captures the fast canonical runtime LoRA states, including existing Prepare, picker, metadata-tooltip and Connections views—not these standalone Designer-reference forms.

All modes share GitHub artifacts (two-day requested retention, subject to the manual Artifact Sweeper), latest-per-PR publication on the compatibility `ci-render-previews` branch and one three-column thumbnail comment. See [UI previews](../../../../guides/ui-previews.md) for details and coverage limits. The separate design references remain available locally through `tools/ui/preview_lora_design.py` after owner authorization; no separate LoRA Action or Drive upload remains.

## Integration boundary

The real Prepare forms are in `src/dtflow/ui/forms/`. The desktop's
`LoraAssetPanel.prepare` now opens the batch editor after file selection.
`ui/prepare_lora_editor.py` collects metadata and authorizes only ready rows.
The existing asset service performs the provider operations with the accepted
managed identifier. The source .ui and shared QSS remain the presentation
authority; no second Prepare layout is maintained here.

Connections, Generate, LoRA picker and metadata management in this folder
are still design references and are not mounted as production forms.
The Prepare screenshot uses synthetic rows in the **real** Qt form. A passing
offscreen capture does not establish Windows/DPI acceptance or backend
eligibility, and no provider transfer is performed by the renderer.
