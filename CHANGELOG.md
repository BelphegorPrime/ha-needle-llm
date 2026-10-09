# Changelog

## 0.5.0 - 2026-10-09

- Add Ollama and OpenAI Conversation model provider adapters in addition to
  the existing llama.cpp integration adapter, reusing HA's loaded clients.
- Normalize native Ollama tool calls and OpenAI Responses function calls
  into the same pre-execution validation pipeline.
- Discover models by **capabilities and loaded conversation subentries**,
  not by requiring HA 2026.10; older unsupported providers are hidden.
- Hide the HA-model routing mode when no compatible provider is loaded.
- Keep Needle-only routing functional on older HA versions; retain mandatory
  Needle approval in all provider-backed routing modes.
- Add regression tests for legacy entries, provider models, tool-call formats,
  unavailable clients, and HA version-independent selection.


## 0.4.3 - 2026-10-08

- Remove the model-only `openai_compatible` backend and associated client,
  tests and configuration fields: every supported route now uses Needle.
- Keep standalone Needle and Needle + existing Home Assistant model modes.
- Keep the OpenAI tool-call *format adapter* needed to consult an existing
  Home Assistant model provider before Needle approval.
- Old standalone model-only config entries fail closed with a clear
  reconfiguration error. Do not reinterpret their previous model URL as
  a Needle server.
- Explain why native Assist calls do not produce a Needle routing trace.


## 0.4.2 - 2026-10-08

- Replace the overloaded options form with two guided pages: choose routing
  mode first, then configure only its relevant fields.
- Show human-friendly backend and preselection names rather than internal
  identifiers and avoid repeating configured llama.cpp connection details.
- Add structured, stage-by-stage `routing_trace` diagnostics including
  preselection, independent Needle approval, argument generation,
  HA execution and timings.
- Preserve original raw diagnostics, provide explicit failed stages and
  avoid falsely declaring devices missing when no lookup occurred.
- Add tests for conditional configuration forms and routing trace results.


## 0.4.1 - 2026-10-08

- Assign each HA provider-backed routing entry its own stable LLM API ID,
  even when it shares a Needle URL with an existing Needle-only integration.
- Preserve existing Needle-only API IDs and conversation API selections.
- Add regression coverage for the provider/Needle ID collision.


## 0.4.0 - 2026-10-08

- Add a provider-backed routing mode referencing an existing Home Assistant
  llama.cpp conversation model by config entry and subentry IDs.
- Reuse the loaded model client, credentials and endpoint without copying them.
- Support Needle or model preselection followed by independent Needle
  confidence-gated approval, and final argument extraction by the model.
- Prevent preliminary model proposals from being executed. Final model calls
  receive only the approved native Assist tool schema.
- Keep the existing Needle and OpenAI-compatible HTTP modes for compatibility.
- Add multilingual-safe literal entity-name matching and regression tests for
  Needle/model disagreement and low-confidence refusal.


## 0.3.1 - 2026-10-08

- Rename the generic OpenAI-compatible backend, client, route tool, validation
  and test modules to avoid implying a dependency on llama.cpp.
- Preserve compatibility with the initial `llama_cpp` backend setting.
- Fix successful OpenAI-compatible tool execution diagnostics when no Needle
  discovery shortlist exists.
- Clarify that this API adapter targets an OpenAI-compatible inference HTTP
  endpoint; invoking arbitrary Home Assistant Conversation agents would
  bypass the intended pre-execution validation.


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
