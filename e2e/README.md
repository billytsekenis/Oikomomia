# End-to-end tests (Playwright)

Requires a running server with a registered user (`E2E_EMAIL` / `E2E_PASSWORD`, default `test@example.com` / `testpass123`).

```bash
npm i -D playwright          # or use a global Playwright install
node flows.js                # 22 user journeys, desktop + iPhone 13; writes screenshots to docs/screenshots
node mobile_overflow.js      # checks every page for horizontal overflow at iPhone width
```
`BASE_URL` overrides the target (default `http://localhost:8000`).
