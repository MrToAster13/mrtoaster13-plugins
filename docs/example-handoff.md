# Handoff 007: add OAuth login

Status: in-progress

## Goal
Let users sign in with Google instead of only email/password. Adding an OAuth
flow behind a feature flag so we can ship it dark and enable per-tenant.

## Done
- Added `oauth` provider config to `config/auth.ts` (Google client id/secret via env).
- Stubbed the redirect route `GET /auth/google` — builds the consent URL, redirects.
- Wrote the failing test `auth/oauth.test.ts::redirects to Google consent`.

## Next (in order)
1. Implement the callback route `GET /auth/google/callback` — exchange code for
   tokens, upsert the user, set the session cookie. Make the failing test pass.
2. Put the whole flow behind the `oauth_login` flag (default off).
3. Add the "Sign in with Google" button to `views/login.tsx` (flag-gated).

## Key decisions
- Store the OAuth refresh token, not just the access token — because we need
  offline calendar access later. Column already added in migration `0042`.
- Reuse the existing `sessions` table, no new session model — keeps logout logic
  in one place.
- Google only for v1. Other providers are a later flag, not this PR.

## Relevant files
- `config/auth.ts` — provider config; the new `oauth` block lives here.
- `routes/auth.ts` — redirect route done, callback route is the TODO.
- `auth/oauth.test.ts` — the failing callback test drives the next step.
- `migrations/0042_oauth_tokens.sql` — token columns, already applied.

## Gotchas / open questions
- The Google client secret is only in `.env.local`, not in CI yet — the callback
  test will fail in CI until we add the secret. Flagged to infra, not resolved.
- Consent URL scope list is minimal (`openid email profile`). Calendar scope is
  deliberately deferred — don't add it here.
