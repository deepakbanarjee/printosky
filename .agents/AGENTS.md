# Printosky Agent Rules

- **Git-First Sync**: Always run `git fetch` (and check remote status/pull if necessary) at the very start of every task to check for changes on GitHub. The codebase is modified from multiple locations, and GitHub is the single source of truth.

## SumatraPDF & Store Printing Memory (Updated 2026-08-05)

- **Portable SumatraPDF Executable**: The local executable `SumatraPDF.exe` (usually in the project folder) must be the **portable reader executable** (extracted from the `.zip` archive on the official downloads page), NOT the installer `.exe` renamed. The installer binary opens the reader UI but throws `ParseFlags: argName: '-print-to'` errors when running command-line print tasks.
- **Redirection Logic**: In collection/finishing-only stores (like Nattika / `PRINTK`), there is no Konica printer. The `store_config.json` has `konica_ip` set to `null` or `""`. This correctly routes any B&W jobs to the Epson printer queue (e.g. `EPSON EM-C8100 Series`).
- **Sequential Slicing & Imposition**: 
  - Mixed-colour jobs group consecutive sheets of the same color mode (B&W or Colour) and send them as sequential print jobs to preserve collation order in the output tray.
  - N-up grids are filled sequentially (not odd/even split) for slide/handout formats.

