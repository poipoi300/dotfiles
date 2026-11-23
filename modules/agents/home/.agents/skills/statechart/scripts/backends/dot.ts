import { type Chart, type ChartState, type ChartStep, type Region, type Rule, tupleKey } from "../ir.ts";

// One step of a bug path in chart terms: step n moves between two chart states of a region.
export type PathMove = { n: number; region: number; from: string[]; to: string[]; label: string };

// One figure: the whole chart with one bug path numbered, or with no path when no rule breaks.
// `on` and `loop` hold stateKey()s of the states on that path and in its repeating part.
export type Figure = {
  name: string;
  rule: Rule | undefined;
  moves: PathMove[];
  on: Set<string>;
  loop: Set<string>;
  stutters: boolean;
};

export const stateKey = (region: number, path: string[]) => tupleKey([region, path]);

const circled = (n: number) => (n <= 20 ? String.fromCodePoint(0x2460 + n - 1) : `(${n})`);
const quote = (s: string) =>
  `"${s.replaceAll("\\", "\\\\").replaceAll('"', '\\"').replaceAll("\n", "\\n")}"`;
// HTML-like labels: entities for markup, a doubled backslash so Graphviz does not expand \N or
// \G, and a line break for a newline, which Graphviz would otherwise drop.
const html = (s: string) =>
  s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("\\", "\\\\")
    .replaceAll("\n", "<BR/>");
const startsWith = (path: string[], prefix: string[]) =>
  prefix.length <= path.length && prefix.every((p, i) => path[i] === p);

// The drawn step that carries a path move: the same target, a source equal to or above the
// move's source (lift.ts moves a shared exit up to the parent), and preferably the move's label
// as one of the step's alternatives (lift.ts joins steps that share both ends with " or ").
export function stepOf(region: Region, move: PathMove): ChartStep | undefined {
  const fits = region.steps.filter(
    (s) => tupleKey(s.to) === tupleKey(move.to) && startsWith(move.from, s.from),
  );
  const named = fits.filter((s) => ` or ${s.label} or `.includes(` or ${move.label} or `));
  return (named.length ? named : fits).sort((a, b) => b.from.length - a.from.length)[0];
}

// A state is marked only when no combination of region states through it is clean.
function marks(s: ChartState) {
  if (!s.breaks.length || s.works) return { kind: undefined, lines: [] as string[] };
  const always = s.breaks.filter((b) => b.always).map((b) => b.rule.name);
  const sometimes = s.breaks.filter((b) => !b.always).map((b) => b.rule.name);
  const lines: string[] = [];
  if (always.length) lines.push(`✕ breaks ${always.join(", ")}`);
  if (sometimes.length) lines.push(`⚠ can break ${sometimes.join(", ")}`);
  return { kind: always.length ? "breaks" : "mayBreak", lines };
}

// Steps from a start state to each state and parent state, walking the drawn steps.
function depths(region: Region) {
  const depth = new Map<string, number>();
  const set = (path: string[], d: number) => {
    for (let i = 1; i <= path.length; i++) {
      const k = tupleKey(path.slice(0, i));
      if (!depth.has(k)) depth.set(k, d);
    }
  };
  let frontier = region.states.filter((s) => s.start).map((s) => s.path);
  for (const path of frontier) set(path, 0);
  for (let d = 1; frontier.length; d++) {
    const next: string[][] = [];
    for (const step of region.steps)
      if (frontier.some((f) => startsWith(f, step.from)) && !depth.has(tupleKey(step.to))) {
        set(step.to, d);
        next.push(step.to);
      }
    frontier = next;
  }
  return depth;
}

export function dotLines(chart: Chart, figure: Figure) {
  const out: string[] = [];
  let nodes = 0;
  let clusters = 0;
  const ids = new Map<string, string>();
  const idOf = (r: number, path: string[]) => {
    const k = stateKey(r, path);
    let id = ids.get(k);
    if (id === undefined) ids.set(k, (id = `s${nodes++}`));
    return id;
  };
  out.push(`digraph ${quote(figure.name)} {`);
  out.push(
    `  graph [rankdir=TB, compound=true, nodesep=0.35, ranksep=0.55, pad=0.1, fontname="sans-serif", fontsize=10, labeljust=l];`,
    `  node [shape=box, style="rounded,filled", fontname="sans-serif", fontsize=11, margin="0.14,0.06", height=0.3, class="state"];`,
    `  edge [fontname="sans-serif", fontsize=10, arrowsize=0.6, class="step"];`,
  );
  if (chart.title) out.push(`  label=${quote(chart.title)}; labelloc=t;`);
  const many = chart.regions.length > 1;
  for (const [r, region] of chart.regions.entries()) {
    const base = many ? "    " : "  ";
    if (many) out.push(`  subgraph cluster_${clusters++} {`, `    label=${quote(region.name)}; class="region";`);
    // Bands are the parent states, when state() returns arrays.
    const bandOf = (s: ChartState): string[] => s.path.slice(0, -1);
    // Order bands by where the walk first reaches each level, so the flow reads in order.
    const seen = new Map<string, number>();
    const bands = new Map<string, { band: string[]; states: ChartState[] }>();
    for (const [i, s] of region.states.entries()) {
      const band = bandOf(s);
      for (let d = 1; d <= band.length; d++) {
        const k = tupleKey(band.slice(0, d));
        if (!seen.has(k)) seen.set(k, i);
      }
      const k = tupleKey(band);
      const entry = bands.get(k) ?? { band, states: [] };
      entry.states.push(s);
      bands.set(k, entry);
    }
    const rank = (band: string[]) => band.map((_, d) => seen.get(tupleKey(band.slice(0, d + 1))) ?? 0);
    const ordered = [...bands.values()].sort((x, y) => {
      const a = rank(x.band);
      const b = rank(y.band);
      for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
      return a.length - b.length;
    });
    // Start points first: Graphviz breaks cycles in declaration order, so the flow starts left.
    for (const s of region.states)
      if (s.start) {
        const id = idOf(r, s.path);
        out.push(
          `${base}${id}_start [shape=point, width=0.1, label="", class="start"];`,
          `${base}${id}_start -> ${id} [class="step start${figure.on.has(stateKey(r, s.path)) ? " path" : ""}"];`,
        );
      }
    const clusterOf = new Map<string, string>();
    const open: string[] = [];
    let pad = base;
    const close = () => {
      open.pop();
      pad = pad.slice(2);
      out.push(`${pad}}`);
    };
    for (const { band, states } of ordered) {
      while (open.length && !startsWith(band, open)) close();
      for (let d = open.length; d < band.length; d++) {
        const name = `cluster_${clusters++}`;
        clusterOf.set(tupleKey(band.slice(0, d + 1)), name);
        out.push(`${pad}subgraph ${name} {`, `${pad}  label=${quote(band[d])}; class="band";`);
        open.push(band[d]);
        pad += "  ";
      }
      for (const s of states) {
        const id = idOf(r, s.path);
        const key = stateKey(r, s.path);
        const on = figure.on.has(key);
        const loops = figure.loop.has(key);
        const { kind, lines } = marks(s);
        const name = html(s.path.at(-1) ?? "");
        const rows = [on ? `<B>${name}</B>` : name];
        for (const line of lines) rows.push(`<FONT POINT-SIZE="9">${html(line)}</FONT>`);
        if (loops)
          rows.push(`<FONT POINT-SIZE="9">↻ ${figure.stutters ? "stays here" : "repeats"} forever</FONT>`);
        const cls = ["state", on && "path", kind, loops && "repeats"].filter(Boolean).join(" ");
        out.push(`${pad}${id} [label=<${rows.join("<BR/>")}>, class="${cls}"];`);
      }
    }
    while (open.length) close();
    const numbers = new Map<ChartStep, number[]>();
    for (const m of figure.moves)
      if (m.region === r) {
        const step = stepOf(region, m);
        if (step) numbers.set(step, [...(numbers.get(step) ?? []), m.n]);
      }
    const isState = new Set(region.states.map((s) => tupleKey(s.path)));
    const depth = depths(region);
    // A step from or to a parent state attaches to the parent's band (ltail/lhead).
    const end = (path: string[]) => {
      if (isState.has(tupleKey(path))) return { id: idOf(r, path), cluster: undefined };
      const leaf = region.states.find((s) => startsWith(s.path, path))?.path ?? path;
      return { id: idOf(r, leaf), cluster: clusterOf.get(tupleKey(path)) };
    };
    for (const step of region.steps) {
      const from = end(step.from);
      const to = end(step.to);
      const text = step.label + (step.when ? ` [${step.when}]` : "");
      const ns = numbers.get(step);
      const attrs = ns
        ? [`label=<<B>${html(`${ns.map(circled).join(" ")} ${text}`)}</B>>`, `class="step path"`]
        : [`label=${quote(text)}`];
      // A step back toward the start does not rank: the flow reads from the start outward.
      if ((depth.get(tupleKey(step.to)) ?? 0) < (depth.get(tupleKey(step.from)) ?? 0))
        attrs.push("constraint=false");
      if (from.cluster) attrs.push(`ltail=${from.cluster}`);
      if (to.cluster) attrs.push(`lhead=${to.cluster}`);
      out.push(`${base}${from.id} -> ${to.id} [${attrs.join(", ")}];`);
    }
    if (many) out.push("  }");
  }
  out.push("}");
  return out;
}
