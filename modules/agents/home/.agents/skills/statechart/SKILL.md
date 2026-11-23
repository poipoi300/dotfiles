---
name: statechart
description: Use only when the user types /statechart, or asks to model-check with TLA+ a system that moves between states (a product flow, a protocol, or a service), or to show a bug as a path through states. Checks the model with TLC and writes a report with the shortest path to each broken rule plus a Graphviz chart for each bug; load the dataviz skill too, to draw the chart.
metadata:
  source: https://github.com/cursor/plugins/tree/d42aff08aa58965d2735c4ead61a9af82b89d0d0/pstack/skills/statechart
---

# Statechart

The chart shows how a system moves between states, in the words of the person who reads it. That person, the reader, decides whether the behavior is right.

## Run

Work in a scratch folder outside the repo. Write a TLA+ spec and its config. Then run:

```sh
<skill dir>/scripts/statechart SPEC.tla [--config FILE] [--view FILE] [--out DIR]
```

`scripts/statechart` is a POSIX shell script that runs `scripts/statechart.ts` with Node, Bun, or Deno. TLC comes from the `tla-tools` package in the arcus `agents` module, and the launcher points `TLA2TOOLS` at its jar, so a run needs no network. TLC runs on the system `java` unless `JAVA` names another binary. If the jar is missing, install the package through the system-changes skill.

The command writes `report.md`, `tlc.out`, and one chart for each rule that breaks, `chart-RULE.dot`, with that rule's shortest path numbered as in the report. When every rule holds, it writes `chart.dot`.

## Write the spec

- Write each rule that the reader agrees to as an INVARIANT. A rule can be about a screen, a result, a stored value, or a message. If the model has no variable for a rule, add the variable. Never weaken a rule to make it hold.
- Write `Next` as a disjunction of the actions, such as `YouPressPlay \/ YouPressStop`. If you do not, TLC names every step `Next`.
- A state with no step out breaks `NoDeadlock`, also when it is a correct end state. When an INVARIANT or a PROPERTY already has that name, the rule is `CHECK_DEADLOCK`. Add to `Next` an action that is true only in the end states and changes nothing, such as `Done == phase \in {"paid", "failed"} /\ UNCHANGED vars`, and give it a label in `step()`.

## Label the chart

You do the work that needs judgment: the words on each state and arrow, the title, and the report to the reader. Put your work in a view module, and give it with `--view`.

To show the reader's words, the view exports `state(vars)`. The value that `state()` returns picks the form:

- A string gives a flat chart: `export const state = (v) => (v.paid ? "Paid" : "Paying");`
- An array gives parent states, drawn as a box around their children: `export const state = (v) => v.main.split(".");`. Use them to group states into phases.
- An object gives one parallel region for each key: `export const state = (v) => ({ Playback: v.playback, Volume: v.volume });`

Label each state with what the reader can see or cares about, such as "Paying" or "Lease held, write pending". Keep variable names out of labels. In `vars`, a TLA+ sequence is an object with keys from 1, so `v.pc[1]` is `pc[1]`. A set is an array.

The view can also export `step(action, from, to)`. It gets the TLC action name and the values before and after the step, and it returns the label of the arrow. Write each step label as the user sees it happen, such as "you press play" or "the connect fails". Do not name the code or the TLA+ action. Two actions that end the same in the chart get the same label, so that they share one arrow.

When a step can occur only in some conditions, end its label with the condition in brackets, in the reader's words, such as `you press play [while the file loads]`. In a regions chart, the command writes the condition on the other regions on the arrow, in the words of their labels, such as `[when Offline]`. Your brackets replace it, and two arrows from one state can then look the same. So in a regions chart, do not write brackets for a condition on another region. When a value that no label shows decides where a step goes, name the result in the label, such as "the roster read fails".

The view can also export `title`, a string that the chart shows at the top.

The command calls `state()` at most once for each model state, and `step()` at most once for each step. The chart and the report both use those answers. So make each function depend only on its arguments.

The chart does not draw a step that stays in one label, and a bug-path step like that leaves a gap in the chart's numbers. Split a label when the reader must see such a step.

Aim for about 20 arrows or fewer, because a busier chart is hard to read. First return coarser labels from `state()`. Give one label to values that act the same, and leave out a detail that the reader does not need, such as a call in flight whose return changes nothing. Keep each state of a bug path, and keep each mark. When a merge turns "✕ breaks" into "⚠ can break", it hides where a rule breaks. Do not merge two states that break different rules, because the reader then cannot see which rule breaks where. If the chart is still too busy, return `null` from `step()` for the steps that the reader does not need, such as a reset that every state has, or a step whose arrow repeats what other arrows show. Obey these rules when you hide steps:

- Keep each chart state reachable by arrows from a start state.
- Keep at least one arrow out of each chart state that has a step out in the model.
- Hide all the steps of an arrow, or none of them. When you hide only some, its condition gets narrower.
- In the hand-off, say which steps the chart hides.

## Read the report and the chart

- A state that breaks a rule says so on the state: "✕ breaks RULE", or "⚠ can break RULE" when only some model states behind it break the rule. A state in the repeating part of a PROPERTY counterexample says "↻ repeats forever" or "↻ stays here forever". In a regions chart, a state gets a mark when each combination of region states that includes it can break a rule. When a combination can break a rule and none of its states has a mark, each state in that combination gets a mark.
- In `chart-RULE.dot`, the states on that rule's shortest path are bold, and each arrow on it starts with its step number from the report. Arrows that lead back toward the start do not push states down the chart, so it reads from the start outward.
- TLC goes past a state that breaks a rule, so the chart shows the states after a bug.
- Because TLC goes past bugs, a model with no limit on its states can run without end. Keep the constants small, or add a CONSTRAINT.
- With a VIEW or a SYMMETRY, TLC counts states that it treats as the same as one state, and the chart draws that one state. An arrow can lead to it rather than to the state that the step reaches, and the report says so.
- TLC checks the rules in each state past a CONSTRAINT or an ACTION_CONSTRAINT, but it does not explore that state or check it for deadlock. When TLC's shortest path for a rule ends in such a state, the chart draws that state, and the report gives that path. The chart does not draw the other states past the bound. A deadlock in such a state does not show. TLC also checks the rules after each step that an ACTION_CONSTRAINT excludes, also when it reaches the next state another way. When TLC's shortest path for a rule ends with such a step, the chart draws that step.
- The command checks the rules with TLC on the config as written, with one worker. When a rule breaks, the command removes that rule and runs TLC again, until no rule breaks. After the first break, each run stops at 100,000 distinct states, so that a large model does not run without end. When a run reaches that bound or stops on an error, the report lists the rules that it did not check under "Rules not checked", and the chart does not mark them. A rule that is not checked can hold or break. To check it, remove the rules that break from the config, or make the model smaller. The bound adds a module that changes TLC's order of the variables, so for a property TLC can find a different counterexample than a run without the bound.
- The chart has a budget of 500 states, counted by TLC on the config as written. Above that, the chart draws only the paths to the rules that break, and when no rule breaks, the command writes no chart. It still checks each rule, and the report gives the state count and each bug path. To see the whole graph, make the model smaller.
- `tlc.out` is what TLC printed on the config as written. When the chart does not show the bug path of a rule that breaks, `tlc-RULE.out` is what TLC printed for the run that found it. When TLC repeats its first warning, plain TLC prints it twice, but `tlc.out` shows it once, because TLC leaves the second copy out of the output that this command reads. That happens only when Java runs with `-XX:+UseParallelGC`, since otherwise TLC's first warning is its advice to pass that flag, which it prints once.

## Draw it

Load the dataviz skill and follow it for color, text, light and dark themes, and the final look. The `.dot` file fixes the layout and the words. Lay it out with Graphviz (`dot -Tsvg chart-RULE.dot -o chart-RULE.svg`). If the result is much wider than where it will be shown, as charts with regions often are, lay it out again with `-Grankdir=LR`. Graphviz draws grey boxes and black text that ignore the theme, so give each class its look with CSS:

- `state`: every state. `path`: a state or arrow on the numbered path, which leads the eye. `breaks`, `mayBreak`: the states that break a rule; they are status, and their text already names the rule. `repeats`: the repeating part of a PROPERTY counterexample. `start`: the start dot and its arrow.
- `band`: a parent state's box. `region`: a parallel region's box.

Where the dataviz skill is not available, keep the text in the theme's ink, give `breaks` and `mayBreak` a red and an amber tint, and make the path darker than the other arrows.

In T3, show the SVG with `html_preview` and then `html_render`. Elsewhere, give the SVG's path.

## Hand off

Give each chart to the person who must decide. Add one line for each bug: the rule, the reader's sentence for that rule, and the path as the report numbers it. Say what the model leaves out. Give the command that makes the files again, with a new `--out` folder.

When the model describes code, replay each bug path against the real code, as a test or a live repro, one step for each action. If a bug does not occur, think that the model is wrong until you show that it is not.
