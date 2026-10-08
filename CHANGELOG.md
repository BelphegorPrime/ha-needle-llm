# Changelog

## 0.1.0 - 2026-10-08

Initial release.

- Add Home Assistant config flow for a local Needle server.
- Validate the server through `GET /model`.
- Register a selectable Home Assistant LLM API.
- Add the `NeedleRoute` LLM tool.
- Route `HassTurnOn` and `HassTurnOff` through Needle.
- Limit v0.1 routing to Assist-exposed light and switch entities.
- Add confidence, grounding, ambiguity and exposure validation.
- Keep Needle stateless by resetting it before requests by default.
- Add HACS metadata, tests and CI.
