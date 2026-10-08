# Changelog

## 0.3.0 - 2026-10-08

- Add optional llama.cpp routing backend using OpenAI tool calling.
- Allow backend, model ID and server URL selection in initial setup and options.
- Keep Needle as the default and preserve all existing Needle installations.
- Route to native Home Assistant Assist tools through a single llama.cpp call.
- Reject malformed, unavailable or multiple tool calls before native HA
  schema, exposure and execution checks.
- Do not fabricate Needle confidence values for llama.cpp responses.
- Add locale-aware server instructions and structured route diagnostics.


## 0.2.7 - 2026-10-08

- Narrow final Needle argument schemas when the original utterance contains
  exactly one name of a currently Assist-exposed Home Assistant entity.
- Remove only the optional device_class field from the Needle-facing schema,
  avoiding noisy repeated enum values and hallucinated "blind" classes.
- Preserve the original native schema for Home Assistant validation and
  reject target name/domain mismatches before invoking any native tool.
- Remain language-independent: match names verbatim rather than translating
  or hard-coding language-specific light and switch semantics.
- Expose target-schema narrowing decisions in NeedleRoute diagnostics.


## 0.2.6 - 2026-10-08

- Guard against hallucinated device_class filters even when Needle reports
  high confidence and valid syntax.
- When a name appears explicitly in the original user request and matches
  exactly one exposed Home Assistant entity, remove an unrequested conflicting
  device_class and restrict the call to the matched entity's real domain.
- Do not repair ambiguous targets, conflicting domain filters or explicitly
  requested conflicting classes. Keep Home Assistant as the final authority.
- Include target_guard repair details in the conversation trace.


## 0.2.5 - 2026-10-08

- Keep operation-first guidance only in the discovery pass.
- Send the original user utterance verbatim to final Needle argument extraction.
- Restore unmodified native tool descriptions during final routing.
- Show explicitly when no Home Assistant lookup or action was attempted, so the
  outer conversation model does not invent a device-not-found explanation.


## 0.2.4 - 2026-10-08

- Make dynamic discovery operation-first instead of device-type-first.
- Add lightweight action-parameter hints derived from each native Home
  Assistant schema without hard-coding individual tools.
- Tell Needle not to invent optional settings such as color, brightness,
  volume or position when the user only requested a generic action.
- Apply the same grounding guidance to the narrowed full-schema routing pass.
- Add a routing strategy marker to diagnostics.


## 0.2.3 - 2026-10-08

- Move dynamic narrowing unit tests into the established validation test module
  to satisfy Ruff/isort consistently across the supported Python versions.
- Releases are now created only after the full Validate workflow succeeds.


## 0.2.2 - 2026-10-08

- Fix Ruff/isort spacing in the dynamic narrowing test module.


## 0.2.1 - 2026-10-08

- Fix Ruff import ordering in the dynamic narrowing tests so the complete CI
  matrix can validate the 0.2.x routing implementation.


## 0.2.0 - 2026-10-08

- Add dynamic two-stage Needle tool narrowing.
- First send all current Home Assistant Assist tools with lightweight empty
  parameter schemas so Needle only chooses relevant capabilities.
- Use executable and suppressed discovery candidates only as non-executable
  shortlist hints.
- Send at most three shortlisted tools with their full native schemas in a
  second Needle pass.
- Keep the existing confidence, grounding, schema and native Home Assistant
  validation gates exclusively on the second pass.
- Add separate discovery and routing diagnostics and timings.


## 0.1.7 - 2026-10-08

- Fix Ruff ASYNC109 in the editable configuration flow.
- Confirm HACS, hassfest and test matrix pass on Home Assistant 2024.7.4,
  2025.6.3 and 2026.10.0.


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
