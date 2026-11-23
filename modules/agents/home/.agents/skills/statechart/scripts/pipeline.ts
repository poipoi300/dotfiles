import { dotLines, type Figure, type PathMove, stateKey } from "./backends/dot.ts";
import { plainText } from "./backends/plain-text.ts";
import { report } from "./backends/report.ts";
import {
  type Answers,
  type Bug,
  type Chart,
  type CheckedView,
  type CheckResult,
  type Graph,
  type Labels,
  must,
  PART,
  ruleKey,
  type Story,
  type View,
  type Walk,
} from "./ir.ts";
import { asParents, ask, labelGraph, storiesOf } from "./passes/label.ts";
import { lift } from "./passes/lift.ts";
import { loopCheck } from "./passes/loop-check.ts";
import { bugs, drawOrder, walk } from "./passes/reach.ts";
import { regionsLie } from "./passes/region-check.ts";
import { drawRegions } from "./passes/regions.ts";

function* inParts(lines: string[]) {
  let start = 0;
  let size = 0;
  for (let i = 0; i < lines.length; i++) {
    size += lines[i].length + 1;
    if (size > PART || i === lines.length - 1) {
      yield (start ? "\n" : "") + lines.slice(start, i + 1).join("\n");
      start = i + 1;
      size = 0;
    }
  }
}

const shortestFirst = (a: { path: unknown[] }, b: { path: unknown[] }) =>
  a.path.length - b.path.length;

// One figure per bug, each numbering that bug's path in the labels the chart was drawn with.
function figuresOf(chart: Chart, labels: Labels, walked: Walk, found: Bug[]): Figure[] {
  const names = chart.regions.map((r) => r.name);
  const shape = (id: string) => labels.shapes[must(walked.index, id)];
  if (!found.length)
    return [{ name: "chart", rule: undefined, moves: [], on: new Set(), loop: new Set(), stutters: false }];
  return found.map((bug) => {
    const seq = [bug.start, ...bug.path.map((p) => p.state)];
    const keys = (ids: string[]) =>
      new Set(ids.flatMap((id) => names.map((name, r) => stateKey(r, shape(id)[name]))));
    const moves: PathMove[] = [];
    for (const [k, p] of bug.path.entries()) {
      const from = shape(seq[k]);
      const to = shape(p.state);
      const label = labels.drawnLabels[must(labels.pathStepIndex, p.step)] ?? p.action;
      for (const [r, name] of names.entries())
        if (JSON.stringify(from[name]) !== JSON.stringify(to[name]))
          moves.push({ n: k + 1, region: r, from: from[name], to: to[name], label });
    }
    return {
      name: `chart-${bug.rule.name}`,
      rule: bug.rule,
      moves,
      on: keys(seq),
      loop: bug.loop === undefined ? new Set() : keys(seq.slice(bug.loop)),
      stutters: bug.stutters === true,
    };
  });
}

export function project(graph: Graph, walked: Walk, view: View, found: Bug[]) {
  const { order, rank } = drawOrder(walked);
  const draw = (answers: Answers) => {
    const labels = labelGraph(graph, walked, answers, rank, found);
    return { labels, chart: drawRegions(walked, labels, order, view) };
  };
  const answers = ask(graph, walked, view);
  const first = draw(answers);
  const lie = regionsLie(first.chart.regions, first.labels, walked, order);
  const { labels, chart } = lie ? draw(asParents(walked, answers)) : first;
  loopCheck(chart, labels, walked, found);
  const lifted = lift(lie ? { ...chart, lie } : chart);
  return {
    chart: lifted,
    figures: figuresOf(lifted, labels, walked, found),
    stories: storiesOf(graph, found, walked, first.labels),
  };
}

export function compile(result: CheckResult, view: CheckedView) {
  const walked = walk(result.graph);
  const found = [...(result.paths ?? bugs(result.graph, walked))].sort(shortestFirst);
  for (const p of result.properties) if (p.bug) found.push({ rule: p.rule, ...p.bug });
  const { chart, figures, stories } =
    found.length || !result.paths
      ? project(result.graph, walked, view, found)
      : { chart: undefined, figures: [] as Figure[], stories: new Map<Bug, Story>() };
  const text = report(result, found, chart, stories);
  const files = new Map<string, Iterable<string | Uint8Array>>();
  if (chart) for (const f of figures) files.set(`${f.name}.dot`, inParts(dotLines(chart, f)));
  files.set("report.md", [text]);
  files.set("tlc.out", plainText(result.messages));
  const drawn = new Set<string>();
  for (const b of found) if (b.rule.kind !== "property") drawn.add(ruleKey(b.rule));
  for (const { rule, outcome, messages } of [...result.rules, ...result.properties])
    if (outcome === "broken" && messages && !drawn.has(ruleKey(rule)))
      files.set(`tlc-${rule.name}.out`, plainText(messages));
  return { report: text, files, exit: result.exit };
}
