# Changelog

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
