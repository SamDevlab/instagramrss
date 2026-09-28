# Instagram Stories RSS architecture

The application now separates onboarding, collection, persistence, and publishing.

- Onboarding accepts an Instagram Story permalink.
- The permalink is parsed into username and seed Story id.
- A direct provider resolves the numeric Instagram user id.
- Refresh cycles list current Stories through the existing persistent Instagram session.
- Provider Story ids are used as logical identity.
- Media files are stored locally and identified by SHA-256.
- current.json stores the last complete active snapshot.
- state.json stores refresh timestamps and status.
- catalog.json retains known active and inactive media.
- RSS points to local /media URLs instead of temporary CDN links.
- Failed or partial cycles keep the previous complete snapshot.

The legacy Playwright collector remains in the repository for diagnostic use, but the persistent source API uses the direct provider.
