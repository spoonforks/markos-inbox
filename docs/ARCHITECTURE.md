# Architecture and security

The Windows launcher composes the Inbox service, assistant services, recorder,
and animation UI. The FastAPI application serves browser chat and the mobile
Inbox. Both share the same sanitized YAML configuration and create private
runtime state only beneath ignored paths.

Inbox items have one fresh SQLite table, `inbox_items`. Captures are classified
through the selected local endpoint. High-confidence items publish directly;
low-confidence items wait for desktop review. There are no visualization tables,
legacy migrations, or secondary knowledge-browser features.

The PWA's static shell may be cached for offline use. Requests under `/api/` are
never intercepted or cached. The sync token is never accepted in a URL and the
server suppresses access logs by default. Local storage is appropriate for a
private personal device, but anyone with access to that browser profile can read
the token; remove the site data from a lost or shared device and rotate the token.

The optional process manager passes an argument list directly to `Popen` with
`shell=False` and retains the child handle. Stop and restart operate only on that
handle, preventing the app from terminating an unrelated local AI process.
