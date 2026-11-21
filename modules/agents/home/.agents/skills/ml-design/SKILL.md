---
name: ml-design
description: Use when writing, changing or reviewing anything that trains, tunes, selects or evaluates a model - training loops, hyperparameter searches, data splits, metrics, experiment logging, result tables or plots of runs.
---

# ML design

## Overview
A result is only as trustworthy as the choices behind it: what was optimized, which checkpoint was kept, which data scored it, and how the result is reported. Make each of those explicit and consistent.

## Selection and reporting
- **Decide before you run.** Write down the result that would change the decision and the smallest effect worth having. If the per-run spread is larger than that effect, choose enough seeds, folds, or evaluation data for the gap's uncertainty to fall below it. Fix that count before running, and do not add runs after seeing the result.
- **One objective, shown first.** The metric a search or early stopping optimizes is the one a results page colors by, sorts by and plots by default. Nothing else is headlined.
- **Metrics come from the checkpoint you keep.** Record every metric (loss, accuracy, any secondary score) at the selected checkpoint, named `best_*`, for example `best_val_loss` and `best_val_accuracy`, plus `best_epoch`. Values from the last epoch are diagnostics, named `last_*`. Never leave a metric unprefixed when both exist; the name must say which checkpoint it came from.
- **Selection data is not test data.** Early stopping, checkpoint choice and hyperparameter search use a validation split. A number reported as "how good it is" comes from data that played no part in any choice. If no such split exists, say the number is optimistic.
- **Know the noise floor.** With n validation examples, one example is 1/n of accuracy. Differences near that floor, or picked as the best of many trials, are partly luck (winner's curse). Re-score the top candidates on held-out data or several seeds before calling a winner.
- **Size the evaluation to the search.** When many trials hit the validation floor (for example 0 errors on a few hundred examples), the split no longer ranks them and the search optimizes its tie-break instead. Before raising the trial count, make the evaluation able to tell configurations apart: k-fold cross-validation, a larger validation split, or a harder dataset. A tuned model that does not beat a simple baseline on test is a sign the task was too easy to tune on.
- **Divergence is a result.** Record diverged or failed runs with a flag and keep them in the data; give the optimizer a defined penalty value instead of dropping them.
- **Rule out what else the number measures.** Before trusting a score, check with evidence that every example was scored (errors, timeouts and skipped rows counted, not dropped), that the model actually produced the scored predictions (no cached or stale outputs), that compared sides were tuned alike, that the gap holds across seeds or folds, and that the evaluation matches the use the number is claimed for. Leakage is under Data integrity.
- **Keep the spread with the number.** Every headline number carries its run count (seeds, folds) and spread. Speed numbers (step time, samples per second, inference latency) follow the benchmark-checklist skill.

## Data integrity
- Fit scalers, encoders and feature statistics on training data only, after the split.
- Split by group or time when rows are related or ordered; check that no entity or exact or near-duplicate row straddles the split.
- Time series: no statistic, lag or window may see the future; compare against the fair naive baseline (seasonal naive where seasonality exists).
- Always score a trivial baseline (majority class, mean, last value) next to the model.

## Training mechanics
- **Default stack:** fastai `Learner` + `fit_one_cycle` + `SaveModelCallback` monitoring the validation objective, not a bespoke loop that keeps the last epoch.
- **Eval mode:** `model.eval()` and `torch.no_grad()` at evaluation, with no train-time augmentation in validation or test.
- **Metric aggregation:** aggregate over examples, not as a mean of per-batch means when the last batch is uneven.
- **Seeds:** seed everything, including DataLoader workers. Results from one seed are reported as such.
- **Device:** never hardcode it.

## Experiment records
- One row per trial with every hyperparameter and every `best_*` / `last_*` metric, stored as Parquet.
- Log what is needed to rerun a trial: seed, data version, code version.

## Common mistakes
| Mistake | Fix |
|---|---|
| Search optimizes `best_val_loss`, page colors by last-epoch accuracy | Color, sort and plot by the objective; record accuracy at the best epoch |
| `val_loss` next to `best_val_loss` | Rename to `last_val_loss` |
| Best of 10,000 trials reported as the expected score | Re-score the top-k on held-out data or seeds |
| Test set used to pick the epoch | Pick on validation, report on test |
| 10,000 trials on a validation split where hundreds score 0 errors | k-fold CV or a harder dataset, sized before the search |
| Examples that error are dropped and accuracy is reported on the rest | Count them as failures, or report the share scored next to the number |
| "B beats A by 0.4%" from one seed each | Several seeds or folds per side; the gap must clear the uncertainty of the difference (see Decide before you run) |
