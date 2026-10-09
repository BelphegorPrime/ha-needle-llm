# Needle LLM for Home Assistant

Needle LLM is a Home Assistant custom integration that places
[Cactus Compute Needle](https://github.com/cactus-compute/needle) between a
general conversation model and Home Assistant's native Assist LLM tools.

```text
Assist
  -> conversation model (for example Qwen via llama.cpp)
  -> NeedleRoute
  -> Needle lightweight discovery
  -> dynamically narrowed native Assist tool set
  -> Needle full-schema routing
  -> validation / confidence gate
  -> native Home Assistant Assist tool
```

The larger model keeps responsibility for natural conversation. Needle is used
as a compact, schema-constrained tool router. Home Assistant remains the
authorization and execution boundary.

## Companion Needle add-on

This repository provides the **Home Assistant custom integration**. To
run a local Needle server on Home Assistant OS or Supervised, install the
[Needle add-on](https://github.com/BelphegorPrime/ha-addon-collection/tree/master/addon-needle) from the
[Home Assistant add-on collection](https://github.com/BelphegorPrime/ha-addon-collection).
The add-on hosts Needle; this integration makes its tool routing available to
compatible LLM conversation agents.

For an alternative without an LLM conversation agent, see the
[add-on's custom-sentence Assist guide](https://github.com/BelphegorPrime/ha-addon-collection/blob/master/addon-needle/FULL_ASSIST_SETUP.md).

> [!IMPORTANT]
> Version 0.1.0 is an initial integration. Test it with harmless devices before
> using it for security-sensitive actions.

## Features

- UI configuration through **Settings -> Devices & services**
- Connects to a local Needle server and validates it with `GET /model`
- Registers a selectable Home Assistant LLM API with a stable ID derived from the Needle URL
- Exposes one `NeedleRoute(query)` tool to the conversation model
- Dynamically mirrors the tools provided by Home Assistant's native **Assist**
  LLM API
- Uses two-stage routing: lightweight semantic discovery first, then full-schema
  routing against only the dynamically shortlisted tools
- Automatically follows the capabilities of the installed Home Assistant
  version instead of maintaining a hard-coded list of domains or intents
- Routes device control, live-state queries, scripts, timers, climate, lights,
  covers, media and other Assist tools when Home Assistant exposes them
- Validates Needle-selected arguments with the original Home Assistant tool
  schema before execution
- Preserves Home Assistant's own exposure rules, intent/tool validation and
  conversation context
- Explicitly resets Needle before every routed request so no prior Needle conversation state can influence tool selection
- Rejects low-confidence, suppressed-only, multi-call, ungrounded and
  unavailable-tool responses

## Home Assistant compatibility

The minimum supported Home Assistant version is:

```text
2024.7.0
```

The integration contains compatibility handling for the major LLM API changes
between Home Assistant 2024.7 and current releases, including:

- Voluptuous vs. Probatio LLM schemas
- legacy dictionary tool results vs. `ToolResult`
- the older `async_register_api()` API that had no unregister callback
- newer tool annotations without requiring them on older releases

CI tests the integration against representative Home Assistant releases from
2024, 2025 and 2026.

The exact tools available through Needle depend on the **Assist API provided by
your installed Home Assistant version**. New Home Assistant Assist tools can
therefore become available without adding a new hard-coded mapping here.

## Requirements

- Home Assistant 2024.7.0 or newer
- A reachable Needle 3 server exposing:
  - `GET /model`
  - `POST /reset`
  - `POST /complete`

For Home Assistant OS / Supervised installations, you can run the local Needle
server using the companion
[Needle Home Assistant add-on](https://github.com/BelphegorPrime/ha-addon-collection/tree/master/addon-needle).

The **add-on** hosts the Needle inference server; **this custom integration**
exposes Needle to compatible Home Assistant conversation agents as an Assist
LLM API. Install both when you want to use the add-on for LLM tool routing.
See the [add-on setup guide](https://github.com/BelphegorPrime/ha-addon-collection/blob/master/addon-needle/DOCS.md)
for its installation and network configuration.

## Installation with HACS

Until this repository is included in the default HACS catalog:

1. Open HACS.
2. Open **Integrations**.
3. Add this repository as a **Custom repository**:
   ```text
   https://github.com/BelphegorPrime/ha-needle-llm
   ```
4. Select category **Integration**.
5. Install **Needle LLM**.
6. Restart Home Assistant.
7. Go to **Settings -> Devices & services -> Add integration**.
8. Search for **Needle LLM**.

## Manual installation

Copy:

```text
custom_components/needle_llm
```

to:

```text
/config/custom_components/needle_llm
```

and restart Home Assistant.

## Configuration

The config flow asks for:

| Option | Default | Description |
| --- | --- | --- |
| Needle URL | - | Base URL of the Needle server, for example `http://192.168.1.50:7860` |
| Minimum confidence | `0.80` | Minimum Needle confidence required before a routed call can execute |
| Request timeout | `30 s` | Timeout for one Needle HTTP request |

After setup you can change the URL, minimum confidence and request timeout via
**Settings -> Devices & services -> Needle LLM -> Configure**. Saving options
reloads the integration automatically. The timeout can be set up to 600
seconds.

Home Assistant Core runs in a separate container from add-ons, so do not use
`localhost` unless your deployment explicitly makes that work.

## Using it with an LLM conversation integration

After configuring Needle LLM, edit a conversation integration that supports
Home Assistant LLM APIs, for example the native `llama.cpp` integration on
Home Assistant versions that provide it.

Select the API named similar to:

```text
Needle LLM @ 192.168.1.50:7860
```

If you want all Home Assistant tool use to pass through Needle, disable the
normal **Assist** LLM API for that conversation agent and enable only Needle
LLM.

The conversation model then sees one Home Assistant routing tool:

```text
NeedleRoute(query)
```

A request can then follow this path:

```text
User
  -> Qwen / another conversation model
  -> NeedleRoute(original request)
  -> current native Assist tool schemas
  -> Needle chooses one tool
  -> integration validates the response
  -> native Home Assistant Assist tool executes
  -> result goes back to the conversation model
```

For example, on a Home Assistant release that exposes these tools, Needle might
select:

```text
intent__HassTurnOff
homeassistant__GetLiveContext
light__SetLight
script__...
```

Tool names and exact capabilities are owned by Home Assistant and may differ
between releases.

## Safety model

Needle output is treated as untrusted input.

A routed call is executed only when all of these conditions are true:

1. Needle returned `success: true`.
2. Exactly one entry exists in `function_calls`.
3. Confidence meets the configured threshold.
4. `validation.ungrounded` is empty.
5. The selected tool exists in the **current native Assist API instance**.
6. The arguments pass the original Home Assistant tool schema.
7. Home Assistant's own tool or intent implementation accepts the request.

`suppressed_calls` are never executed.

The integration does **not** expose the unrestricted Home Assistant service
registry to Needle. It only mirrors tools that Home Assistant itself provides
through the native Assist LLM API.

Entity exposure, areas, supported features and authorization remain Home
Assistant's responsibility. If Home Assistant would reject a target through
the native Assist tool, Needle LLM does not bypass that check.

For security-sensitive devices such as exterior doors, locks, gates and alarm
systems, use dedicated Home Assistant safeguards and confirmation logic. A
confidence score is not an authorization mechanism.

## Why the tool set is dynamic

Earlier prototypes hard-coded `HassTurnOn`, `HassTurnOff`, `light` and
`switch`. The integration no longer does that.

For every `NeedleRoute` invocation it obtains the current native Assist API
instance from Home Assistant and serializes the tools Home Assistant provides
for that request/context.

It then uses two Needle passes:

1. **Discovery:** all current tools are sent with their native names, titles and
   compact operation descriptions, but with empty parameter schemas. The
   descriptions are enriched dynamically from each native schema so Needle can
   distinguish direct actions from device-specific settings without hard-coded
   tool mappings.
2. **Routing:** only the discovered candidates (up to three) are sent again with
   their complete native Home Assistant schemas and original descriptions.
   Critically, this pass receives the **unaltered original user request**, not
   the discovery instructions. This avoids confusing tool argument extraction
   with meta-instructions. Only this second result is eligible for execution.

A low-confidence or suppressed discovery candidate is safe to use as a
shortlist hint because discovery never executes anything. The second pass still
has to satisfy the configured confidence threshold, grounding checks, native
schema validation and Home Assistant's own authorization/execution rules.

This has several advantages:

- no duplicated Home Assistant capability list;
- large unrelated schemas no longer compete with the relevant tool schema;
- new native Assist tools can work without adding domain-specific dispatcher
  code here;
- exposure and validation remain in Home Assistant;
- older releases naturally expose the smaller tool set they support;
- newer releases can expose newer capabilities.

If a Home Assistant tool schema cannot be serialized into a JSON schema usable
by Needle, that tool is skipped and an error is written to the Home Assistant
debug log rather than weakening validation.

## Diagnostics

Home Assistant's **Show details** output includes structured diagnostics for
both successful and rejected routes. Depending on the failure stage this can
include:

- separate discovery and final-routing reset/`/complete` timings;
- transport exception type and HTTP status;
- Needle confidence, reasoning, validation and suppressed calls;
- Needle prefill/decode throughput and peak RAM;
- number of native Assist tools discovered and the final candidate shortlist;
- tools skipped because their schema could not be serialized;
- selected native Home Assistant tool and arguments;
- the stage at which execution stopped.

This is intended to make routing failures diagnosable without lowering the
confidence threshold or enabling debug logging first.

## Stateless routing

Needle LLM explicitly calls `POST /reset` before every `POST /complete`.
Although the Needle playground also resets/rebuilds its agent while handling
`/complete`, the explicit reset makes the integration's stateless-routing
contract unambiguous and prevents prior Needle conversation state from
influencing a new Home Assistant request.

The client holds one lock across reset + complete so concurrent Home Assistant
requests cannot interleave Needle state.

```text
Home Assistant / conversation model = conversation history
Needle                              = one-turn tool router
```

## Development

Create a virtual environment appropriate for the Home Assistant release you
want to test.

For the current environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pip install homeassistant
```

Run:

```bash
ruff check .
pytest
```

GitHub Actions additionally runs:

- tests on representative Home Assistant 2024, 2025 and 2026 releases
- HACS validation
- hassfest

## Versioning

The custom integration is versioned independently from the Needle Home
Assistant add-on.

Current version:

```text
0.5.3
```

## License

Apache-2.0. See [LICENSE](LICENSE).


### Target-aware schema simplification

For a user utterance that **literally names exactly one Assist-exposed entity**,
the final Needle tool schema omits optional `device_class` classification
arguments. Home Assistant still receives arguments validated against the
**full native schema**. Generated names/domains that contradict the uniquely
named exposed entity are rejected before an action is dispatched.

When no unique exposed entity name is present, the original full schema is
used unchanged; low-confidence and suppressed Needle calls are never executed.


## Existing Home Assistant model integration + Needle approval (v0.4.0)

The **HA model provider** mode reuses a previously configured Home Assistant
conversation model instead of asking for another API URL, model name or key.

**Supported provider adapters:** Home Assistant's built-in `llama_cpp`,
`ollama`, and `openai_conversation` integrations, wherever their currently
loaded model client and conversation subentry APIs are compatible. The
selected loaded config entry exposes an existing `AsyncOpenAI` client, and the
selected conversation model is read from its HA config subentry. Only
`entry_id:subentry_id` is stored in this integration. The model client is
resolved anew for every turn, including after a normal reload of the
upstream provider integration.

### Configure

1. Configure a compatible **llama.cpp**, **Ollama**, or **OpenAI Conversation**
   integration with a conversation model in Home Assistant first.
2. Install/update this integration, open **Settings → Devices & services →
   Needle LLM** and configure a new entry (or an existing Needle entry).
3. Select backend **`ha_provider`**, supply the existing **Needle server URL**
   (Needle is still mandatory), choose **Existing Home Assistant model** from
   the provider dropdown, and select one of:
   - **`needle_preselection`**: Needle proposes the native Assist tool.
   - **`model_preselection`**: the selected HA model proposes the tool.
4. Set the **minimum Needle confidence** (default 0.8) and timeout, then save.
   Choose the resulting **NeedleVerifiedRoute** LLM API in your conversation
   agent and test a harmless device.

The pipeline is:

```text
HA native Assist tool catalog
  -> Needle or configured HA model: tool preselection (no execution)
  -> Needle: independent action approval (confidence/grounding gate)
  -> configured HA model: final arguments for ONLY the approved tool
  -> HA schema validation + exposure/target checks
  -> native Assist execution
```

The model's preliminary tool call is never executed. Needle's approval uses
the full catalog of compact tool descriptions; it must independently choose
the same native tool at/above the configured confidence threshold. An
unavailable target, ambiguous tool, mismatched action, missing tool call,
untrusted model output or validation failure results in **no action**. No
language-specific mappings or fixed smart-home domain lists are used.

The supported configurations always include Needle: either standalone
Needle routing or Needle plus an existing HA model provider. The old
model-only backend was intentionally removed because it bypassed Needle.

**Compatibility:** Selecting an arbitrary HA conversation agent cannot
guarantee safe tool proposal interception. Each supported provider uses its
known loaded config entry/subentry interfaces and existing client methods. Future providers will have individually
tested adapters instead of trying to invoke `conversation.process` and risk
implicit tool execution or recursion.

**Latency:** Both strategies still run a Needle approval pass. This is a
correctness/safety-focused prototype, not yet proven faster than native HA
tool calling. Benchmarking remains essential.


## Clearer setup and execution details (v0.4.2)

The configuration wizard has **two steps**. First select one of the two
Needle-powered modes. The second page displays **only settings relevant** to it:

| Mode | Settings shown |
| --- | --- |
| Needle + existing HA model | Existing HA model, preselection strategy, Needle URL, minimum Needle confidence, timeout |
| Needle only | Needle URL, minimum Needle confidence, timeout |

Provider-backed routing **never asks you to re-enter** your llama.cpp endpoint,
API key or model ID. The provider picker reuses your existing HA conversation model.
Switching modes preserves hidden settings, and all existing config entries stay
compatible.

### How to verify that the router was actually used

In your conversation agent, select the **Needle + HA model** tool API and call
**`NeedleVerifiedRoute`**. If the Assist trace shows only
`light__HassLightSet` or `intent__HassTurnOff` as the *outer* tool,
your conversation model may be using the **native Assist API directly**,
not our integration. Check that the agent isn't also given the native
Home Assistant Assist tool API alongside the Needle router, then retry.
The native Assist tools are still used *internally* after approval.

### New routing details

When the router actually receives a call, its tool result contains a
`routing_trace` alongside `diagnostics` with:
- Executed/rejected status, routing backend, strategy, selected tool,
  and original request language.
- A chronological breakdown of tool preselection, independent Needle
  approval, final argument generation and native HA execution.
- Needle confidence and approval result, the tool chosen in each stage,
  model-proposed arguments and the final validated arguments.
- Measured stage durations and, on successful native responses, total
  end-to-end latency and the target entities reported by Home Assistant.
- Explicit failure stage on rejected routes. No false claim of an HA
  device lookup when execution never reached Home Assistant.

The trace is diagnostic JSON, not a frontend-only visualization.
No underlying model HTTP credentials are included. Device names and user
commands may still appear in the local Assist trace, so review before sharing.


## Needle-only routing modes (v0.4.3)

**Standalone OpenAI-compatible model routing has been removed.** It bypassed
Needle and duplicated the functionality of existing Home Assistant model
integrations. Only these modes remain:

- **Needle + existing Home Assistant model** (recommended): choose the loaded
  HA conversation model and whether the model or Needle preselects a tool.
  Needle must independently approve the action. The HA model then generates
  the arguments for the approved tool only.
- **Needle only:** Needle performs tool selection and argument generation;
  native Home Assistant Assist validation still governs execution.

Existing Needle-based configurations keep their saved settings. An old
`openai_compatible` or `llama_cpp` router entry (from the experimental
0.3.x releases) **will not run or silently fall back** to a different model:
its setup explicitly fails until you reconfigure it to use a real Needle URL
and one of the supported modes, or remove that obsolete entry.

**Trace visibility:** A successful native `light__HassLightSet` action by
itself does *not* establish that Needle was used. In your conversation agent,
select the Needle-provided LLM API (for the provider-backed mode, look for
**Needle + HA model** / **NeedleVerifiedRoute**) and do not grant the outer
agent a competing direct native Assist tool API for a controlled comparison.
The integration can provide detailed Needle timings and decisions only when
its own tool was invoked.

For a real `NeedleVerifiedRoute` invocation, the `routing_trace` field
contains stage-by-stage tool proposals, Needle confidence/approval, model
arguments, native HA target results, and durations; `diagnostics` retains
the full raw details. Direct native HA calls are outside this routing trace.


## Multiple existing HA model providers and older HA support (v0.5.0)

The **Needle + existing Home Assistant model** mode can now reuse any loaded,
compatible conversation subentry from the following built-in integrations:

| Model integration | Existing client API | Model selection |
| --- | --- | --- |
| llama.cpp | OpenAI-compatible Chat Completions | `chat_model` in a conversation subentry |
| Ollama | Native `ollama.AsyncClient.chat` | `model` in a conversation subentry |
| OpenAI Conversation | OpenAI Responses API | `chat_model` in a conversation subentry |

These are **provider adapters**, not extra Needle-bypassing modes. The model
only proposes a tool or generates its arguments; **Needle still independently
approves the selected action**. The native HA Assist permission checks and
execution remain unchanged.

### Automatic compatibility detection

Provider discovery checks each running HA integration for:

1. An enabled, **loaded** supported config entry.
2. A `conversation` **config subentry** containing the model ID.
3. A currently available, matching **model client API** on `runtime_data`.

This is based on **runtime capabilities, not a hard-coded HA version check**.
For example, Ollama and OpenAI Conversation gained compatible subentry-based
configuration in HA 2025.7. On older versions without it, those entries are
**not shown** in the model dropdown, and the provider-backed routing mode is
hidden if **no compatible model** is available. Needle-only routing is always
selectable. llama.cpp becomes available on HA installations that provide its
built-in integration and compatible conversation subentries.

If an existing configured model is removed, unloaded, disabled, or becomes
incompatible following an HA upgrade, the router **fails closed**. It will not
silently invoke a different provider or execute a native Assist action.
An unavailable saved model is not offered as an apparently working option.

The provider adapter reads the client from HA on every request. Provider
URLs, authentication headers, API keys and passwords are **not copied** into
Needle LLM's config entry. Other models and cloud providers may require
provider-specific adapters; generic support must not be inferred merely from
their advertised OpenAI compatibility.

**Privacy:** Choosing OpenAI Conversation sends the prompt and relevant tool
descriptions to OpenAI under that already configured HA account. Local
llama.cpp/Ollama instances keep inference local if configured accordingly.

### Compatibility policy

- Keep the existing minimum HA compatibility and CI testing against
  HA 2024.7, 2025.6, and 2026.10.
- Dynamically hide unsupported providers on older versions rather than
  forcing all users to update Home Assistant.
- Add feature- and provider-specific regression tests. Avoid duplicating
  service credentials or attempting to call a provider Conversation agent
  through `conversation.process` (which may itself execute tools).


### Approval of room-level requests (0.5.1)

When the selected HA model proposes an action, Needle evaluates the raw user
request using empty-argument schemas and real competing tools from the
proposed tool's native family (e.g. turn-on versus turn-off). If a tool has no
same-family alternatives, the full catalog remains available rather than
forcing a single choice. This is an independently checked decision; it can
still reject at the unchanged minimum confidence of 0.8 or for negation,
ungrounded arguments, and disagreements. The approver never executes devices.

For commands such as "Schalte Licht im Wohnzimmer ein", the final HA model is
encouraged to use the area and domain fields if available, rather than
inventing a particular device name. Home Assistant still performs the native
target/exposure and parameter checks before any execution.

The trace includes `diagnostics.needle_approval.candidate_tools`,
`candidate_tool_count`, and `query_mode=original_user_request`.


### More focused Needle action approval (v0.5.2)

When a configured HA model proposes a tool, Needle still independently
evaluates the *original user request* with action alternatives and the
unchanged minimum-confidence threshold. Similar **action names** within a
native tool family are compared first, instead of sending unrelated actions
(e.g. timer cancellation when deciding between turn-on and turn-off). This
reduces irrelevant distractors but **does not guarantee approval**: Needle's
own confidence, negation, grounding and action agreement checks still apply.

A command like "Schalte Licht im Wohnzimmer ein" may target all exposed
lights in the area, without naming a particular lamp. The eventual HA
arguments should be grounded in the actual request (e.g.
`{"area": "Wohnzimmer", "domain": ["light"]}`) and still pass HA's
native validation. Low-confidence Needle refusals do not constitute device
lookup failures and must not be described as missing or ambiguous entities.


### Needle approval semantics (v0.5.3)

The approval stage compares the model's proposed native Assist action with
real alternatives (for instance, turn-on versus turn-off). It now includes
the **native action description** alongside the short title, without any
parameters or inferred targets. In multilingual requests this provides
more information for Needle's semantic decision, though it does not
guarantee higher confidence.

A matching operation is **not itself permission to execute**: Needle's
confidence and validation gates still apply. If both tools agree but
Needle's confidence is below the minimum, the trace explicitly reports
that the request was not executed; it does not claim any particular
devices were missing or that multiple matches were found.


### Experimental English approval retry for low-confidence agreement

For **Needle + existing HA model**, an optional second approval attempt is
triggered automatically when Needle and the HA model already independently
selected the **same action**, but Needle's original-language confidence is
below the configured threshold. The configured threshold (default **0.80**)
is never lowered.

The already-configured HA model translates the original request into English
through an **argument-free, non-executable translation tool**. Exposed literal
entity names are checked for preservation. Needle then receives the same real
action alternatives and independently evaluates the translated request.
Execution can continue only when the translated Needle result passes the
original confidence threshold, chooses the **same** original action, contains
no action arguments or suppressed calls, and passes validation. Otherwise
the original low-confidence rejection stands.

The original user utterance is always used for **final HA argument generation**.
The translation is diagnostic data, never a Home Assistant target or executable
instruction. The detailed trace includes an `english_fallback` record under
`diagnostics.needle_approval`, including the original and retry confidence.
This path adds up to one HA-model inference and one Needle inference per
fallback and may increase latency.

**Important:** Translation can change meaning even when entity names stay
unchanged; the agreement checks reduce this risk but cannot eliminate it.
Treat the mechanism as experimental. Test only harmless devices until a
multilingual safety benchmark validates the behavior. Requests explicitly
rejected by Needle's negation/grounding checks are never rescued.
