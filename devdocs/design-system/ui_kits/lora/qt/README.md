# Editable Qt Designer LoRA design references

These five `.ui` forms translate the owner-reviewed [LoRA layout](../index.html) and
[design contract](../../../lora-ux.md) into **native Qt Widgets** that can be edited with
Qt Designer or inspected with the offline renderer. They are **design-only**: none are loaded
by the DTFlow application, and they do not upload, persist, prepare, or generate anything.

| Form | Review surface |
| --- | --- |
| `connections_lora.ui` | Three existing workspace regions; compact five-switch group on the right and a Custom LoRAs library collapsed by default in the center |
| `generate_lora.ui` | Current selection and weights in the right inspector, without a duplicate metadata library |
| `prepare_lora_batch.ui` | Five illustrative source files in a six-column, one-row-per-file editor; explicit Prepare action and managed-name preview |
| `lora_picker.ui` | Selected-first, alphabetical visual list and an independent execution-order example |
| `lora_metadata_editor.ui` | One-record metadata editor over Connections; Cancel and Save metadata only |

The exact architecture choices, names, weights, connection status, and counts shown here
are **synthetic design samples**, not claims about the currently connected backend.

## Previewing native Qt forms

Use DTFlow's locked development environment from the repository root:

```powershell
uv run --frozen python tools/ui/render_lora_design.py --out tmp/lora-design --connections-expanded --compact-prepare
```

The command loads each design form with the **shared DTFlow Qt stylesheet**, writes PNGs
under ignored `tmp/lora-design/`, and does not connect to a provider. Use
`--view connections` (or `generate`, `prepare`, `picker`, `metadata`) to
render a single form. `--connections-expanded` captures the same Connections form
with the library content shown. `--compact-prepare` captures the same horizontal
six-column batch editor at 760 px wide.

On Windows, inspect Qt font/DPI behavior separately. The offline screenshots are
synthetic design evidence, **not** runtime behavior or Windows acceptance.

## Integration boundary

The canonical application forms remain in `src/dtflow/ui/forms/` (in particular
`main_window.ui`, `generation_page.ui`, and `lora_selection_editor.ui`).
These prototypes intentionally do not overwrite them. Runtime implementation must
reuse canonical forms, existing services, the shared theme, and issue #373's
approved behavior after the required Prepare-contract amendment.

The open/closed library toggle, editor overlay, picker selection, and controls here
are **not wired** in these static reference forms. Render controls are inspection
aids, not a second implementation of DTFlow interactions.
