# Changelog

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
