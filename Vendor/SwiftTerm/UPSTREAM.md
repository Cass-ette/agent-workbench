# SwiftTerm source

- Upstream: https://github.com/migueldeicaza/SwiftTerm
- Tag: `1.99.3`
- Commit: `282c3c3a932b55c129628876b70f37d993af1615`
- License: MIT; see `LICENSE`.

This vendored copy keeps the upstream SwiftTerm sources and build-information
generator. The app compiles only the SwiftTerm library source as a local target,
so macOS builds do not fetch unrelated cross-platform dependencies. Two small
source files provide the build metadata and an empty XTGETTCAP table that the
upstream SwiftPM build normally generates. Terminal emulation and local PTY
support are provided by the upstream SwiftTerm implementation.

The macOS host adds an optional copied-byte callback on the process adapter.
It observes the existing PTY output for authenticated remote viewers without
replacing the parser, input transport, or process delegate.
