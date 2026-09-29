# Instagram Stories RSS architecture

The application separates authentication, onboarding, collection, persistence, and publishing.

- Onboarding accepts an Instagram Story permalink.
- The permalink is parsed into username and seed Story id.
- A source stores an optional `auth_connection_id`.
- `AuthConnectionService` resolves the connection without exposing its credential.
- `ProviderFactory` creates one isolated provider context for that source.
- `INSTAGRAM_SESSION` uses the mobile provider with encrypted credentials.
- Legacy sources without `auth_connection_id` can temporarily use `INSTAGRAM_SESSION_FILE`.
- Refresh cycles list current Stories through the existing provider pipeline.
- Provider Story ids are used as logical identity.
- Media files are stored locally and identified by SHA-256.
- current.json stores the last complete active snapshot.
- state.json stores refresh timestamps and status.
- catalog.json retains known active and inactive media.
- RSS points to local /media URLs instead of temporary CDN links.
- Failed or partial cycles keep the previous complete snapshot.

## Authentication boundaries

`source.json` contains only `auth_connection_id`; session IDs and cookies are stored separately in `data/auth/credentials.json` encrypted with AES-GCM. The master key comes from `AUTH_CREDENTIAL_MASTER_KEY` and is never persisted by the application.

The mobile provider receives a resolved credential and does not open the auth store. Each source refresh creates its own provider/client context, so invalidating one connection does not switch another source to its credential.

Connection statuses are `ACTIVE`, `RECONNECT_REQUIRED`, `REVOKED`, and `ERROR`. Authentication failures mark the attached connection as `RECONNECT_REQUIRED` while preserving the last complete snapshot, catalog, state, media, and RSS.

The explicit first-stage bootstrap is `scripts/authorize_instagram_connection.py`. It reads a local session file and prints only public connection metadata. There is no API endpoint for manually pasting a session ID or browser cookies.

`META_OAUTH` is reserved as a capability-scoped provider type. It is not currently implemented as a Graph API adapter and must not be treated as a provider for arbitrary third-party sources.

The legacy Playwright collector remains in the repository for diagnostic use, but the persistent source API uses the provider factory and the mobile provider by default.
