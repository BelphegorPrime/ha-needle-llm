# Needle LLM for Home Assistant

Needle LLM is a Home Assistant custom integration that exposes a guarded LLM API backed by [Cactus Compute Needle](https://github.com/cactus-compute/needle).

It is designed to sit between a general conversation model (for example the native Home Assistant `llama.cpp` integration) and Home Assistant control intents:

```text
Assist
  -> conversation model (for example Qwen via llama.cpp)
  -> NeedleRoute
  -> Needle /complete
  -> validation / confidence gate
  -> Home Assistant intent
  -> exposed entity
```

The goal is to keep natural conversation in the larger model while using Needle as a small, schema-constrained router and an additional validation boundary for device control.

> [!IMPORTANT]
> Version 0.1.0 is an initial integration. It currently routes only `HassTurnOn` and `HassTurnOff` for Assist-exposed `light` and `switch` entities.

## Features

- UI configuration through **Settings -> Devices & services**
- Connects to a local Needle server and validates it with `GET /model`
- Registers a Home Assistant LLM API
- Exposes a single `NeedleRoute(query)` tool to the conversation model
- Builds Needle schemas dynamically from entities exposed to Assist
- Supports area-scoped light/switch control
- Resets Needle before every request by default, keeping routing stateless
- Rejects:
  - low-confidence calls
  - suppressed-only calls
  - multiple calls
  - ungrounded calls
  - non-exposed targets
  - unsupported domains
  - ambiguous entity names
- Preserves the Home Assistant conversation context when invoking intents

## Requirements

- Home Assistant 2026.10 or newer
- A reachable Needle 3 server exposing:
  - `GET /model`
  - `POST /reset`
  - `POST /complete`

For Home Assistant OS / Supervised installations you can use the Needle add-on from:

https://github.com/BelphegorPrime/ha-addon-collection/tree/master/addon-needle

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
| Minimum confidence | `0.80` | Minimum Needle confidence required before an action can execute |
| Reset before each request | enabled | Calls `POST /reset` before `POST /complete` |

Home Assistant Core runs in a separate container from add-ons, so do not use `localhost` unless your deployment explicitly makes that work.

## Using it with llama.cpp

After configuring Needle LLM, edit your llama.cpp conversation agent.

Under the available LLM APIs, select the API named similar to:

```text
Needle LLM @ 192.168.1.50:7860
```

To force supported device-control requests through Needle, disable the normal **Assist** LLM API for that conversation agent and enable only Needle LLM.

The conversation model then sees one control tool:

```text
NeedleRoute(query)
```

For example:

```text
User: Schalte die Wohnzimmerlampe aus.
Qwen: NeedleRoute({"query":"Schalte die Wohnzimmerlampe aus."})
Needle: HassTurnOff({"name":"Wohnzimmerlampe"})
Home Assistant: executes the matching exposed intent target
```

## Safety model

Needle output is treated as untrusted input.

An action is executed only when all of these conditions are true:

1. Needle returned `success: true`.
2. Exactly one entry exists in `function_calls`.
3. Confidence meets the configured threshold.
4. `validation.ungrounded` is empty.
5. The tool is allowlisted.
6. The target is still exposed to the Home Assistant conversation assistant.
7. The entity/area/domain arguments are valid.
8. Ambiguous friendly names are rejected.
9. Home Assistant's own intent matching and authorization still accept the request.

`suppressed_calls` are never executed.

The integration deliberately does not call arbitrary Home Assistant services based on model output.

## Current limitations

Version 0.1.0 supports only:

- `HassTurnOn`
- `HassTurnOff`
- domains:
  - `light`
  - `switch`

State queries, colors, brightness, climate, covers, scripts, media players and other Assist capabilities are planned for later versions.

Needle is currently used as a stateless router. Home Assistant / the conversation model owns the actual conversation history.

## Development

Create a virtual environment and install the test dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

Run:

```bash
ruff check .
pytest
```

GitHub Actions also runs Ruff, pytest, HACS validation and hassfest.

## Versioning

The custom integration is versioned independently from the Needle Home Assistant add-on.

Current version:

```text
0.1.0
```

## License

Apache-2.0. See [LICENSE](LICENSE).
