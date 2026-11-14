# Security policy

## Supported versions

NookCut is pre-1.0. Security fixes go into the latest release and `main`.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through GitHub:
**[Report a vulnerability](https://github.com/nookverse/nookcut/security/advisories/new)**
(Security tab → "Report a vulnerability").

Include what you found, how to reproduce it, and the impact you expect. We aim to acknowledge reports
within 7 days and to agree on a disclosure date with you once a fix is ready.

## Scope and design

Things we especially want to hear about:

- Reading or writing files outside `NOOKCUT_ALLOWED_DIRS` through the MCP tools
- Bypassing the bearer-token check on the HTTP transport
- Command injection through file names, captions or other tool inputs passed to ffmpeg
- The shared service exposing its token or listening beyond localhost

By design NookCut makes no network calls of its own, binds to `127.0.0.1` unless told otherwise,
refuses to listen on other addresses without a token, and stores the shared-service token with
file mode `600`. When running the HTTP server for remote clients, put it behind HTTPS and set
`NOOKCUT_ALLOWED_DIRS`.
