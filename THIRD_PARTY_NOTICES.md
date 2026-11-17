# Third-party software

The root MIT license covers this project's original code. Third-party projects
retain their own licenses and copyright notices.

- The macOS client vendors [SwiftTerm](https://github.com/migueldeicaza/SwiftTerm).
  Its MIT license and source revision are in `Vendor/SwiftTerm/LICENSE` and
  `Vendor/SwiftTerm/UPSTREAM.md`.
- The Windows client uses Tauri, React, xterm.js and other dependencies declared
  in `windows/package.json`, `windows/package-lock.json`,
  `windows/src-tauri/Cargo.toml` and `windows/src-tauri/Cargo.lock`.
  Each dependency retains the license supplied by its upstream package.
- Claude Code and Codex CLI are installed separately and retain their own
  licenses and service terms. This repository does not grant rights to those
  tools or to any model service.
- CCHP Plus is an optional, separately distributed gateway. The deployment
  template references upstream container images; those images are not covered
  by this repository's MIT license. This repository does not redistribute the
  CCHP Plus source or grant rights to its service, images, or API credentials.

The reference pricing JSON files are non-binding snapshots with source URLs.
They are not a promise of model availability or a billing receipt.
