# Remembered file-dialog folders

Every file and folder picker opens where it was last used (#312).

## For developers

- Import `QFileDialog` from `dtflow.ui.file_dialogs`, never from `PySide6.QtWidgets` (ruff `TID251` fails otherwise). The API is the same, so a new picker is remembered from its first use with no declaration.
- A picker is identified by its call site: module, qualified function name and dialog kind. Moving or renaming the calling function starts that picker from the caller's folder once; that is accepted.
- `remember_as="name"` shares one folder on purpose: presets, workflows, generation configuration and mask import/export pairs, and the script input pickers (`script_input_file`, `script_input_folder`).
- `remember=False` opts out; the image export Browse dialog does, because the export destination is itself a setting in `export_preferences.json`.
- The directory a caller passes is only the fallback until something is remembered. Save dialogs keep the suggested file name at the remembered folder.

## Storage and behaviour

`settings.json` key `dialog_paths[runtime][key] = {"path", "title"}`, written once per accepted dialog (cancel stores nothing), with the same limits as other preference files. A missing saved folder falls back to its nearest existing parent, then to the caller's folder; the stored value is never erased by a read. UNC paths are trusted without a check so a dead share cannot stall the UI. An unreadable, malformed or newer settings file means nothing is remembered and shows one warning; pickers keep working. Connection lists every remembered folder with Forget and Forget all.
