# Needle LLM for Home Assistant

Needle LLM is a Home Assistant custom integration that places
[Cactus Compute Needle](https://github.com/cactus-compute/needle) between a
general conversation model and Home Assistant's native Assist LLM tools.

```text
Assist
  -> conversation model (for example Qwen via llama.cpp)
  -> NeedleRoute
  -> Needle /complete
  -> validation / confidence gate
  -> native Home Assistant Assist tool
```

The larger model keeps responsibility for natural conversation. Needle is used
as a compact, schema-constrained tool router. Home Assistant remains the
authorization and execution boundary.

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
- Automatically follows the capabilities of the installed Home Assistant
  version instead of maintaining a hard-coded list of domains or intents
- Routes device control, live-state queries, scripts, timers, climate, lights,
  covers, media and other Assist tools when Home Assistant exposes them
- Validates Needle-selected arguments with the original Home Assistant tool
  schema before execution
- Preserves Home Assistant's own exposure rules, intent/tool validation and
  conversation context
- Keeps routing stateless without a separate `/reset` request; Needle `/complete` already resets or rebuilds the routing agent
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
instance from Home Assistant, serializes the tool schemas that Home Assistant
provides for that request/context, and sends those schemas to Needle.

This has several advantages:

- no duplicated Home Assistant capability list;
- new native Assist tools can work without adding domain-specific dispatcher
  code here;
- exposure and validation remain in Home Assistant;
- older releases naturally expose the smaller tool set they support;
- newer releases can expose newer capabilities.

If a Home Assistant tool schema cannot be serialized into a JSON schema usable
by Needle, that tool is skipped and an error is written to the Home Assistant
debug log rather than weakening validation.

## Stateless routing

The Needle playground server already makes `/complete` effectively stateless
for this use case. If the tool schema is unchanged, it resets the current agent
before completion; if the schema changes, it creates a fresh agent.

Needle LLM therefore does not issue a separate `POST /reset` before each
request. The HTTP client still serializes `/complete` calls with a lock so
concurrent requests cannot interleave Needle state.

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
0.1.2
```

## License

Apache-2.0. See [LICENSE](LICENSE).
