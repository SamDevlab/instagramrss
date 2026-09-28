# instagramrss audit

Before this branch the service performed Story collection synchronously inside HTTP requests. The flow used a persisted Instaloader session, injected those cookies into Playwright, navigated the Instagram Story viewer, derived logical IDs from media URLs, kept media proxy state only in process memory, and published temporary upstream media URLs in RSS.

The new source pipeline introduced on this branch separates:
- Story permalink onboarding;
- numeric Instagram user resolution;
- direct Story listing through the persisted session;
- stable provider Story IDs;
- local media persistence and SHA-256 validation;
- persistent current/state/catalog JSON files;
- scheduler refreshes with per-source locking;
- RSS that references local media files.

Compatibility risk remains around Instagram's unofficial interfaces, session validity, rate limits and challenge/checkpoint responses. Those errors are treated as collection failures and must not replace the last complete snapshot.
