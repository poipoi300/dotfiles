---
name: benchmark-checklist
description: Use when you run a benchmark, or report or act on a speedup, performance regression, throughput, or latency you measured, including training or inference speed. Vets the measurement (limiter, tuning, limits, errors, repeatability, relevance, and whether the work happened) before the number is reported or acted on.
metadata:
  source: https://github.com/cursor/plugins/tree/d42aff08aa58965d2735c4ead61a9af82b89d0d0/pstack/skills/benchmark-checklist
---

# Benchmark checklist

Use this when you produce a performance number: a PR's before and after, a
regression claim, a hillclimb harness, a training or inference speed, or a
library or config choice. Answer each question with evidence from a run, not a
guess about the code.

For a quick ballpark the user asked for, one run is enough. Still check
questions 4 and 7, and say that it is one run. Skip the rest unless that run
looks wrong. A choice between options is never a ballpark.

## Before you run anything

- Write down the claim you expect to make, in the words you would ship
  ("export is 30% faster at p50 on the 60k-row dataset"). The questions test
  that sentence.
- Read the measurement script. Note what it times, counts, and ignores.
- Check the load average with `uptime`, the core count with `nproc`, and for
  GPU work `nvidia-smi`. If the machine is busy, find out what is running.
  Never stop the user's processes. If the GPU is running the user's work, do
  not start a benchmark that competes for its memory or compute: report the
  conflict and wait or ask. CPU load you cannot avoid is shared by
  interleaving the sides so both see the same noise; say so in the report.

## The questions

1. **Why not double?** Name the limiter. Profile in a run you do not report,
   because profilers and tracers slow the work down. Use CPU per process
   (`top`), a runtime profiler (`bun --cpu-prof` or `node --cpu-prof`,
   `torch.profiler`, `uvx py-spy record --subprocesses -o profile.svg --
   <command>`, where `--subprocesses` lets it follow past `uv run`, and `perf`
   or `samply` for native code), GPU utilization (`nvidia-smi dmon`, `nvtop`),
   I/O wait, and syscall counts (`strace -c`), then map the hot spot to source.
   `samply` and `perf` kernel profiling need `kernel.perf_event_paranoid` at 1
   or lower, and `perf` system-wide sampling needs 0 or lower. A missing
   profiler or that setting goes into an arcus module through the
   system-changes skill.
   Watch the load generator and the data loader too: if one saturates first,
   you measured it. If a change did not move the number, the limiter explains
   why, so find it before you call the change useless.
2. **Was it tuned?** Run every side the way production runs it: release
   builds, production flags and env, batching and transaction settings,
   connection pools, caches as warm or cold as production sees them, and the
   same versions and data. If one side runs on defaults, you compared
   configurations, not implementations. A limiter that is a setting (a commit
   per row, a debug build, a missing index, a batch size of 1) means that side
   is untuned. Tune it and measure again before you pick a winner. If you
   cannot tune it, do not pick a winner from that run. When the user is
   choosing what to adopt, narrowing the claim to the code as it ships today
   does not fix this, because they adopt the option, not today's settings.
3. **Did it break limits?** Do the arithmetic. Compare bytes per second with
   disk, network, and memory bandwidth, operations per second times cost per
   operation with your cores or the GPU's peak, and the time saved with the
   time the changed piece took. Removing a piece that takes 10% of the run can
   make the run at most about 11% faster. A result past a limit measured
   something other than the work, such as a cache, a no-op, or a bug.
4. **Did it error?** Count failures and non-success responses, and check that
   outputs are correct, not just present. Rejections are often fast, and
   timeouts and retries are slow. If the script does not count errors, add the
   count.
5. **Does it reproduce?** Before running, fix the run count (at least 5 per
   side; 3 against 3 can never reach significance) and the smallest effect
   worth having. Discard warmup runs or iterations on both sides unless cold
   start is the claim, then run the sides in ABBA or random order so caches,
   lazy initialization, thermal throttling, and drift favor neither. Call it
   faster or slower only when a two-sided rank-sum test at α = 0.05 rejects and
   the median gap is at least that smallest effect. Use
   `scipy.stats.mannwhitneyu(a, b, method=scipy.stats.PermutationMethod())`:
   timings tie, and with ties scipy otherwise falls back to an approximation.
   With 5 runs per side, ranges that do not overlap mean p < 0.01. A test that
   rejects with a smaller gap is a difference below the smallest worthwhile
   effect; a test that does not reject is no measurable difference at this run
   count. Report the median and range, but the range describes the runs and is
   not the test. Do not add runs after seeing the result.
6. **Does it matter?** Next to any micro result, measure the end-to-end path a
   user waits on, with realistic data sizes and concurrency, and report the
   micro result as a share of the whole. A helper that takes 1% of a request
   can make the request at most 1% faster, however fast the helper gets.
7. **Did it even happen?** Confirm the work ran inside the timed region: the
   request reached the server, the rows were written, the bytes were read, and
   the code used the result. Lazy code (generators nobody iterates, promises
   nobody awaits, lazy frames nobody collects, results the JIT can discard),
   asynchronous GPU kernels timed without `torch.cuda.synchronize()` before
   each clock read, and timeouts produce numbers for work that never happened.
   `torch.utils.benchmark.Timer` synchronizes and warms up for you.

## Report

- Lead with the verdict: faster, slower, a difference below the smallest
  worthwhile effect, no measurable difference, or inconclusive.
- Give the number with its unit, the run count, the range, the test's p-value,
  and the limiter. For example, "p50 41 ms → 33 ms, median of 7 runs per side,
  range 32 to 35 ms after, p < 0.001, bound by JSON parsing on one core."
- Call the verdict inconclusive when you claim a difference but cannot name the
  limiter, when a side ran untuned, or when you could not check questions 4
  and 7. Name the gap.
- Keep a PR body to one primary number. Put the runs, range, and limiter
  evidence in a linked artifact or notes file.

## Where else this applies

- A [Hillclimb](../hillclimb/SKILL.md) run on a measured metric vets its
  harness with this checklist before the baseline, and keeps or rejects each
  candidate by question 5's verdict.
- Model quality numbers (accuracy, loss, eval scores) follow
  [ml-design](../ml-design/SKILL.md); its speed numbers (step time, samples
  per second, inference latency) follow this checklist.
