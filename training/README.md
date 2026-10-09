# Low-resource multilingual Needle 3 training (experimental)

This folder implements the synthetic-data and offline-evaluation parts of
[#1](https://github.com/BelphegorPrime/ha-needle-llm/issues/1) and
[#4](https://github.com/BelphegorPrime/ha-needle-llm/issues/4).

**Nothing here changes the Home Assistant conversation integration or
automatically deploys a fine-tuned model.** No local hardware, service,
subscription or external API is used automatically. The curated requests are
synthetic; do not copy real household states, credentials or entity IDs into
the public repository. The corpus is under version control and user review.

## Crucial upstream limitation: local LoRA has no confidence head

The Needle 3.1.3 local training path (as used by the companion add-on)
optimizes its LoRA weights, **not the confidence head**. Upstream's
`needle build --lora` deliberately **drops** the calibration head from a
locally fine-tuned export, so inference reports `confidence: null`.

**Do not replace the production Needle add-on's base `.cact` with that
export.** Our Home Assistant router rejects missing confidence and continues
to require a configured confidence >= 0.8. A successful local training run
does not mean a safe production-ready model. Keep it for experiments on a
separate server. A confidence-capable, independently safety-evaluated fine
tune (possibly via the Needle hosted training platform) is a separate
promotion decision; hosted fine-tuning may incur charges, transfer training
data and require consent. No platform job is launched by these scripts.

## Curated training data

`scenarios.json` has 36 distinct scenario groups, each explicitly labeled
for **German, English, French, Spanish, Italian and Dutch**. These produce
216 reviewed/synthetic examples, including normal and safety-critical
positive commands, negation, status questions, ambiguous/hypothetical
commands, doors versus lights, covers and timers.

Splits are **scenario-group-disjoint**: 24 groups / 144 examples for training,
6 groups / 36 for validation and 6 groups / 36 for testing. Different-language
translations of one scenario can never appear in a different split. These
are *seed* examples, not a validated proof of language coverage and not yet
the planned 200–500 **training** examples in #4. Expand with additional
reviewed scenarios, not automatic paraphrases that leak across splits.

The examples use the upstream Needle 3 `query`, `tools`, `answers`
JSONL format. They focus deliberately on the **argument-free action
approval stage** (native Assist alternatives shown with their reversible
aliases), as this is where the reported German failure occurs. They do
**not** yet train full target/argument extraction, entity access controls
or the entire 21-tool HA toolset. Those remain governed by HA at runtime.

Generate the deterministic split files (stdlib only):

```bash
python3 training/workflow.py prepare
```

This writes `training/out/{train,validation,test}.jsonl`, ignored by Git.

## CPU-only training, with hard operating-system resource limits

Do **not** train inside Home Assistant Core or the always-on Needle add-on.
Use a separate Linux Docker host (a Proxmox VM/LXC with Docker is fine).
Full Needle 3.1.3 fine-tuning still loads a full float32 checkpoint and may
be infeasible on a small Raspberry Pi or a machine with insufficient RAM.
**4 GiB is a hard memory ceiling, not a guarantee the upstream JAX training
can fit.** If JAX exceeds the cap, Docker kills that one training container;
the launcher will never raise limits or retry automatically.

For a first trial, the default container is limited to:

- 1 CPU worth of scheduling, low CPU priority (shares 128, nice +15)
- 4 GiB RAM, **no additional swap**, 128 PIDs
- 1 training sample per batch, LoRA rank 4, max 384-token sequences, 1 epoch
- no GPU; CPU-only JAX, restricted BLAS threads
- **no network during training**, no telemetry, no cloud data generation
- minimum 2 GiB additional **available** host memory before launch
- separate `training/out` workspace; no Home Assistant data directory mounted

Prepare a pinned CPU-only image (image setup downloads dependencies once):

```bash
docker build -f training/Dockerfile -t ha-needle-trainer:3.1.3 .
```

Download the Needle 3 training checkpoint **separately** (explicit
network use; don't check the weights into Git):

```bash
mkdir -p training/out
docker run --rm --cpus 1 --memory 4g --memory-swap 4g \
  -v "$(pwd)/training/out:/work" ha-needle-trainer:3.1.3 \
  needle download needle3.safetensors --out /work
ln -s checkpoints/needle3.safetensors training/out/needle3.safetensors
```

If the symlink already exists, skip the `ln -s` command. Then:

```bash
bash training/run_low_priority.sh train
```

The command runs `needle finetune ... --generate 0 --workers 1` with the
curated `train.jsonl`. There is **no** contact with OpenRouter or any paid
training provider. If the training process is killed by the memory cap,
**that's expected on insufficient hardware** and does not authorize
increasing limits on the HA host. Use a more capable separate host.

Optional bounded limits (`NEEDLE_CPUS` max 2, `NEEDLE_MEMORY_GIB` max 12):

```bash
NEEDLE_CPUS=1 NEEDLE_MEMORY_GIB=6 NEEDLE_RESERVE_GIB=2 \
NEEDLE_EPOCHS=2 NEEDLE_BATCH_SIZE=1 \
  bash training/run_low_priority.sh train
```

Avoid configuring more RAM than the host can spare. If CPU scheduling needs
to stay on a dedicated core, set e.g. `NEEDLE_CPUSET=3`. To stop at any
time, use Ctrl-C or `docker stop <container>`; no background training
service is installed.

### Experimental export only

The local adapter ends at `training/out/needle_lora.safetensors`. To merge
it to a `.cact` strictly for evaluation:

```bash
I_ACCEPT_NO_CONFIDENCE_HEAD=1 bash training/run_low_priority.sh build
```

This runs under the same CPU/RAM limits but **uses network access** because
the upstream `needle build` CLI forcibly downloads a public base archive.
It produces `experimental-uncalibrated.cact`. Do **not** place this in
the production HA add-on as automatic routing weights: it lacks a working
confidence score. Do not commit or publish checkpoints, adapters or weights.

## Read-only benchmark

Evaluate the unchanged base model **before** training. For accurate results,
use a dedicated Needle 3.1.3 playground endpoint, not the HA add-on that may
be serving real conversation requests; both routes call `/reset`, so
sharing one instance can interfere with the other. The benchmark sends only
synthetic requests and zero-argument tool schemas to `/reset` and
`/complete`; it never invokes a Home Assistant service.

```bash
python3 training/workflow.py evaluate \
  --endpoint http://127.0.0.1:7861 \
  --dataset training/out/test.jsonl \
  --output training/out/base-test.json
```

By default this is **serial** (one request at a time), includes a
**1-second pause** between cases and refuses more than 48 cases in one
invocation. Use `--delay 5` if the inference host is already busy.
Use the **same** model/runtime, tool descriptions, dataset and configured
confidence threshold when comparing a candidate.

To score previously collected raw Needle predictions with **no network**
(uses one JSONL object per scenario/locale, each containing a
`response` object in Needle's documented response format):

```bash
python3 training/workflow.py score \
  --dataset training/out/test.jsonl \
  --predictions training/out/base-responses.jsonl \
  --output training/out/base-test.json
```

Measure a candidate against the same held-out samples, then compare:

```bash
python3 training/workflow.py compare \
  --baseline training/out/base-test.json \
  --candidate training/out/candidate-test.json
```

This returns **NO_GO (exit 2)** for any new unsafe approval (especially a
safety-critical action), missing confidence scores, incomplete evaluation,
fewer correct approvals or worsened correct rejections. Passing the
comparison is **not** authorization for production. A separately reviewed
safety evaluation is mandatory.

## Next steps and limitations

1. Expand the training split to **200–500 reviewed training examples** with
   additional scenario-disjoint negative and multilingual cases.
2. Collect a real **base model** benchmark on the **exact** production
   approval tool surface. Consider normalizations only if metrics warrant.
3. Run low-priority local LoRA experiments off the HA server; use an
   experimental Needle endpoint to understand how the action selection
   changes, but expect `confidence: null` with a local export.
4. For production, evaluate a model that **retains a calibrated confidence
   head**. Compare baseline/candidate by locale, approved correctness,
   unsafe-execution rate and latency. Reject any safety regression.
5. Never auto-upload private HA conversation data. An opt-in export/
   anonymization workflow is still tracked in issue #3 and is not required
   for these synthetic experiments.

Upstream sources: [Needle fine-tuning](https://github.com/cactus-compute/needle),
[Needle confidence](https://cactuscompute.com/blog/needle-confidence).
