#!/usr/bin/env bash
# Explicitly bounded CPU-only Needle 3 LoRA training, never run by Home Assistant.
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: training/run_low_priority.sh train|build

Prerequisites:
  python3 training/workflow.py prepare --output training/out
  docker build -f training/Dockerfile -t ha-needle-trainer:3.1.3 .
  needle download needle3.safetensors --out training/out   # run separately

The downloaded checkpoint may be nested under training/out/checkpoints.
Place or symlink needle3.safetensors at training/out/needle3.safetensors.

Environment limits (default: 1 CPU, 4 GiB RAM):
  NEEDLE_CPUS=1
  NEEDLE_MEMORY_GIB=4
  NEEDLE_RESERVE_GIB=2
  NEEDLE_MAX_LEN=384
  NEEDLE_EPOCHS=1
  NEEDLE_BATCH_SIZE=1
  NEEDLE_LORA_RANK=4
  NEEDLE_CPUSET=          # optional Docker CPU affinity
  I_ACCEPT_NO_CONFIDENCE_HEAD=1   # required ONLY for experimental build

A locally merged adapter has NO trained confidence head. Its confidence is
None, so it cannot be used for automatic HA approval under a 0.8 threshold.
This script never modifies the HA add-on or uploads any data or weights.
USAGE
}

mode="${1:-}"
if [[ "${mode}" != train && "${mode}" != build ]]; then
  usage
  exit 2
fi
command -v docker >/dev/null || { echo "Docker is required." >&2; exit 2; }
command -v python3 >/dev/null || { echo "Python 3 is required." >&2; exit 2; }

cpus="${NEEDLE_CPUS:-1}"
memory_gib="${NEEDLE_MEMORY_GIB:-4}"
reserve_gib="${NEEDLE_RESERVE_GIB:-2}"
batch_size="${NEEDLE_BATCH_SIZE:-1}"
max_len="${NEEDLE_MAX_LEN:-384}"
epochs="${NEEDLE_EPOCHS:-1}"
lora_rank="${NEEDLE_LORA_RANK:-4}"
cpuset="${NEEDLE_CPUSET:-}"

# Reject values that could become shell arguments or disable resource limits.
[[ "${cpus}" =~ ^[0-9]+$ && "${cpus}" -ge 1 && "${cpus}" -le 2 ]] ||
  { echo "NEEDLE_CPUS must be 1 or 2." >&2; exit 2; }
for name in memory_gib reserve_gib batch_size max_len epochs lora_rank; do
  value="${!name}"
  [[ "${value}" =~ ^[0-9]+$ && "${value}" -ge 1 ]] ||
    { echo "${name} must be a positive integer." >&2; exit 2; }
done
[[ "${memory_gib}" -le 12 && "${batch_size}" -le 2 &&
   "${max_len}" -le 768 && "${epochs}" -le 5 &&
   "${lora_rank}" -le 16 ]] ||
  { echo "Requested limit exceeds low-resource training safety bounds." >&2; exit 2; }
if [[ -n "${cpuset}" && ! "${cpuset}" =~ ^[0-9,-]+$ ]]; then
  echo "Invalid NEEDLE_CPUSET (expected numbers, commas and hyphens)." >&2
  exit 2
fi

# Avoid thrashing on systems with already-low available RAM. Docker cgroup
# limits are the hard backstop for the training process.
if [[ -r /proc/meminfo ]]; then
  available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  needed_kib="$(( (memory_gib + reserve_gib) * 1024 * 1024 ))"
  if [[ -n "${available_kib}" && "${available_kib}" -lt "${needed_kib}" ]]; then
    echo "Refusing to train: MemAvailable=${available_kib} KiB, need >= ${needed_kib} KiB." >&2
    echo "Use a less busy machine or lower NEEDLE_MEMORY_GIB; no auto-retry." >&2
    exit 2
  fi
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="${repo}/training/out"
checkpoint="${work}/needle3.safetensors"
adapter="${work}/needle_lora.safetensors"
mkdir -p "${work}"
if [[ ! -s "${checkpoint}" ]]; then
  echo "Missing ${checkpoint}: download the Needle 3 training checkpoint first." >&2
  exit 2
fi
if [[ "${mode}" == train && ! -s "${work}/train.jsonl" ]]; then
  echo "Missing train.jsonl; run python3 training/workflow.py prepare." >&2
  exit 2
fi
if [[ "${mode}" == build ]]; then
  [[ "${I_ACCEPT_NO_CONFIDENCE_HEAD:-0}" == 1 ]] ||
    { echo "Build requires I_ACCEPT_NO_CONFIDENCE_HEAD=1 (evaluation only)." >&2; exit 2; }
  [[ -s "${adapter}" ]] ||
    { echo "Missing ${adapter}: train first." >&2; exit 2; }
fi

# The Needle 3.1.3 build command forces a Hugging Face base-archive
# download. Only the explicitly requested experimental build gets network
# access; actual training remains fully offline.
network_mode=none
if [[ "${mode}" == build ]]; then
  network_mode=bridge
  echo "Experimental build may download the public Needle base archive." >&2
fi

args=(
  run --rm --init --name "needle-quiet-train-$$"
  --network "${network_mode}"
  --cpus "${cpus}"
  --cpu-shares 128
  --memory "${memory_gib}g"
  --memory-swap "${memory_gib}g"
  --pids-limit 128
  --oom-score-adj 500
  --cap-drop ALL
  --security-opt no-new-privileges
  --read-only
  --tmpfs /tmp:rw,nosuid,size=512m,mode=1777
  --user "$(id -u):$(id -g)"
  -v "${work}:/work:rw"
  -w /work
  -e JAX_PLATFORMS=cpu
  -e JAX_NUM_CPU_DEVICES=1
  -e JAX_ENABLE_X64=false
  -e JAX_COMPILATION_CACHE_DIR=/work/jax-cache
  -e OMP_NUM_THREADS="${cpus}"
  -e OPENBLAS_NUM_THREADS=1
  -e MKL_NUM_THREADS=1
  -e NUMEXPR_NUM_THREADS=1
  -e XLA_PYTHON_CLIENT_PREALLOCATE=false
  -e HOME=/work
  -e HF_HOME=/work/huggingface
  -e NEEDLE_TELEMETRY=0
  -e DO_NOT_TRACK=1
)
if [[ -n "${cpuset}" ]]; then
  args+=(--cpuset-cpus "${cpuset}")
fi
echo "Starting CPU-only ${mode} with max ${cpus} CPU(s), ${memory_gib} GiB RAM, no swap (network: ${network_mode})."
echo "Training may be VERY slow; if memory exceeds its limit, Docker kills only this container."
if [[ "${mode}" == train ]]; then
  exec docker "${args[@]}" ha-needle-trainer:3.1.3 \
    nice -n 15 needle finetune /work/train.jsonl \
    --checkpoint /work/needle3.safetensors \
    --epochs "${epochs}" --batch-size "${batch_size}" \
    --max-len "${max_len}" \
    --lora-rank "${lora_rank}" --lora-alpha 8 \
    --val-split 0 --generate 0 --workers 1 \
    --seed 42 --checkpoint-dir /work/checkpoints \
    --out /work/needle_lora.safetensors
else
  exec docker "${args[@]}" ha-needle-trainer:3.1.3 \
    nice -n 15 needle build /work/needle3.safetensors \
    --lora /work/needle_lora.safetensors \
    --out /work/experimental-uncalibrated.cact
fi
