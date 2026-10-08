# Changelog

## 0.1.5 - 2026-10-08

- Greatly expand Home Assistant "Show details" diagnostics.
- Include the failure stage, HTTP/transport error type, response status/body,
  reset and completion timings, available/skipped tool counts, Needle routing
  details, performance metrics and the selected Home Assistant tool/arguments.
- Ensure timeout/disconnect errors always include a useful exception type even
  when the underlying Python exception has an empty message.


## 0.1.4 - 2026-10-08

- Restore an explicit `POST /reset` before every `/complete` request.
- Keep reset + complete inside the same client lock so concurrent Home Assistant
  requests cannot interleave Needle state.
- Treat a reset failure as a routing failure instead of continuing with possibly
  stale Needle conversation state.


## 0.1.3 - 2026-10-08

- Include Needle candidate calls, suppressed calls, reasoning, validation data,
  response type and available tool count when a route is rejected.
- This makes low-confidence routing failures diagnosable directly from Home
  Assistant's "Show details" output without lowering the safety threshold.


## 0.1.2 - 2026-10-08

- Make the registered Home Assistant LLM API ID deterministic from the Needle URL.
- Recreating a Needle LLM config entry with the same URL no longer changes the
  API ID and therefore no longer breaks conversation integrations that reference it.


## 0.1.1 - 2026-10-08

- Remove the redundant per-request `POST /reset` request.
- Rely on Needle playground's `/complete` behavior, which resets the current
  agent when schemas are unchanged and creates a fresh agent when they change.
- Keep serialized `/complete` calls to avoid concurrent state interleaving.
- Remove the obsolete reset option from new configuration flows.


## 0.1.0 - 2026-10-08

Initial release.

- Add Home Assistant config flow for a local Needle server.
- Validate the server through `GET /model`.
- Register a selectable Home Assistant LLM API.
- Add the `NeedleRoute` LLM tool.
- Dynamically mirror the native Home Assistant Assist LLM tool set rather than
  hard-coding intents or entity domains.
- Validate Needle-selected arguments using the original Home Assistant tool
  schema before dispatch.
- Delegate execution back to Home Assistant's native Assist API so exposure,
  intent/tool validation and context remain authoritative.
- Add compatibility handling for Home Assistant 2024.7 through current
  releases.
- Add confidence and grounding validation.
- Keep Needle stateless by resetting it before requests by default.
- Add HACS metadata, brand asset, tests and CI.
