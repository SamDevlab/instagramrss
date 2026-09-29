# Instagram Stories RSS architecture

The application separates authentication, onboarding, collection, persistence, and publishing.

- Onboarding accepts an Instagram Story permalink.
- The permalink is parsed into username and seed Story id.
- A source stores `owner_id`, `auth_policy`, and an optional pinned `auth_connection_id`.
- `AuthResolver` selects the connection by owner, scope, status, capability, policy, and deterministic health ranking.
- `AuthConnectionService` stores connection ownership and resolves credentials without exposing them.
- `ProviderFactory` creates one isolated provider context for the selected connection.
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

`source.json` contains the source owner and selection policy. A pinned source may contain `auth_connection_id`; automatic selection is recorded in `last_auth_connection_id` and `last_auth_selection_reason`. Session IDs and cookies are stored separately in `data/auth/credentials.json` encrypted with AES-GCM. The master key comes from `AUTH_CREDENTIAL_MASTER_KEY` and is never persisted by the application.

The mobile provider receives a resolved credential and does not open the auth store. Each source refresh creates its own provider/client context, so invalidating one connection does not switch another source to its credential.

Connections have `owner_id` and `scope` (`PRIVATE` or `SHARED`). Historical connections without these fields migrate conservatively to `owner_id=null` and `PRIVATE`. `PREFER_OWNER_WITH_SHARED_FALLBACK` chooses the owner's active compatible connection, then a shared connection, then legacy. `PINNED` never falls back.

Connection statuses are `ACTIVE`, `RECONNECT_REQUIRED`, `REVOKED`, and `ERROR`. Authentication failures mark only the selected connection as `RECONNECT_REQUIRED` while preserving the last complete snapshot, catalog, state, media, and RSS. Target access failures remain target errors and do not revoke the authentication connection.

The explicit first-stage bootstrap is `scripts/authorize_instagram_connection.py`. It reads a local session file and prints only public connection metadata. There is no API endpoint for manually pasting a session ID or browser cookies.

`META_OAUTH` is reserved as a capability-scoped provider type. It is not currently implemented as a Graph API adapter and must not be treated as a provider for arbitrary third-party sources.

`owner_id` is currently a tenant/bootstrap identifier, not application authentication. The repository does not invent a user login system or treat arbitrary request headers as authenticated identity.

The legacy Playwright collector remains in the repository for diagnostic use, but the persistent source API uses the provider factory and the mobile provider by default.
