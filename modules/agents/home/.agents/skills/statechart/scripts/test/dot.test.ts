import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { dotLines, type Figure, stateKey, stepOf } from "../backends/dot.ts";
import type { Chart, ChartState, ChartStep, Region, Rule } from "../ir.ts";

const rule: Rule = { kind: "invariant", name: "ChargedOnce" };
const state = (path: string[], extra: Partial<ChartState> = {}): ChartState => ({
  path,
  start: false,
  members: [],
  works: true,
  breaks: [],
  ...extra,
});
const step = (from: string[], to: string[], label: string, when?: string): ChartStep => ({ from, to, label, when });
const chart = (states: ChartState[], steps: ChartStep[], name = ""): Chart => ({
  title: undefined,
  regions: [{ name, states, steps, arrows: [] }],
  notes: [],
  inside: 0,
  quiet: 0,
});
const figure = (moves: Figure["moves"], on: string[][] = [], loop: string[][] = [], stutters = false): Figure => ({
  name: "chart-ChargedOnce",
  rule,
  moves,
  on: new Set(on.map((p) => stateKey(0, p))),
  loop: new Set(loop.map((p) => stateKey(0, p))),
  stutters,
});
const line = (lines: string[], needle: string) => {
  const found = lines.find((l) => l.includes(needle));
  assert.ok(found, `no line with ${needle}:\n${lines.join("\n")}`);
  return found;
};

const checkout = chart(
  [
    state(["Cart"], { start: true }),
    state(["Paying"]),
    state(["Paid"]),
    state(["Charged twice"], { works: false, breaks: [{ rule, always: true }] }),
    state(["Declined"], { works: false, breaks: [{ rule, always: false }] }),
  ],
  [
    step(["Cart"], ["Paying"], "you pay"),
    step(["Paying"], ["Paid"], "the card is charged"),
    step(["Paid"], ["Charged twice"], "you press pay again or the retry fires"),
    step(["Charged twice"], ["Cart"], "you start over"),
    step(["Paying"], ["Declined"], "the card is declined"),
  ],
);

test("numbers each path step on its arrow, including a merged 'or' label", () => {
  const lines = dotLines(
    checkout,
    figure(
      [
        { n: 1, region: 0, from: ["Cart"], to: ["Paying"], label: "you pay" },
        { n: 2, region: 0, from: ["Paying"], to: ["Paid"], label: "the card is charged" },
        { n: 3, region: 0, from: ["Paid"], to: ["Charged twice"], label: "the retry fires" },
      ],
      [["Cart"], ["Paying"], ["Paid"], ["Charged twice"]],
    ),
  );
  assert.match(line(lines, "you pay"), /① you pay.*class="step path"/);
  assert.match(line(lines, "the card is charged"), /② the card is charged/);
  assert.match(line(lines, "the retry fires"), /③ you press pay again or the retry fires/);
  assert.doesNotMatch(line(lines, "declined\""), /class="step path"|[①-⑳]/);
  assert.match(line(lines, "Cart</B>"), /class="state path"/);
});

test("writes each mark on its state, so no legend is needed", () => {
  const lines = dotLines(checkout, figure([]));
  assert.match(line(lines, "Charged twice"), /✕ breaks ChargedOnce.*class="state breaks"/);
  assert.match(line(lines, "label=<Declined"), /⚠ can break ChargedOnce.*class="state mayBreak"/);
  assert.doesNotMatch(line(lines, "label=<Paid>"), /break/);
});

test("steps back toward the start do not rank, and forward steps do", () => {
  const lines = dotLines(checkout, figure([]));
  assert.match(line(lines, "you start over"), /constraint=false/);
  assert.doesNotMatch(line(lines, "the card is declined"), /constraint=false/);
  const start = lines.findIndex((l) => l.includes("_start ["));
  const firstBand = lines.findIndex((l) => l.includes("[label=<"));
  assert.ok(start >= 0 && start < firstBand, "the start point is declared before any state");
});

test("parent states become boxes, and a lifted exit leaves from the box", () => {
  const nested = chart(
    [
      state(["Signed out"], { start: true }),
      state(["Trying", "First try"]),
      state(["Trying", "Last try"]),
      state(["Locked"]),
    ],
    [
      step(["Signed out"], ["Trying", "First try"], "you submit"),
      step(["Trying", "First try"], ["Trying", "Last try"], "wrong password"),
      step(["Trying"], ["Signed out"], "you give up"),
      step(["Trying", "Last try"], ["Locked"], "wrong password"),
    ],
  );
  const lines = dotLines(nested, figure([]));
  const box = line(lines, 'label="Trying"; class="band"');
  const cluster = lines[lines.indexOf(box) - 1].match(/subgraph (cluster_\d+)/)?.[1];
  assert.ok(cluster, "the band opens a cluster");
  assert.match(line(lines, "you give up"), new RegExp(`ltail=${cluster}`));
  assert.equal(
    stepOf(nested.regions[0] as Region, { n: 1, region: 0, from: ["Trying", "Last try"], to: ["Signed out"], label: "you give up" })?.label,
    "you give up",
  );
});

test("a repeating part says how it repeats", () => {
  const loopy = chart([state(["Stopped"], { start: true })], []);
  assert.match(line(dotLines(loopy, figure([], [["Stopped"]], [["Stopped"]], true)), "Stopped"), /↻ stays here forever.*repeats/);
  assert.match(line(dotLines(loopy, figure([], [["Stopped"]], [["Stopped"]], false)), "Stopped"), /↻ repeats forever/);
});

test("labels are escaped for DOT and for HTML labels", () => {
  const odd = chart(
    [state(['Say "hi" <b>'], { start: true }), state(["A & B"])],
    [step(['Say "hi" <b>'], ["A & B"], 'it says "go" \\ now')],
  );
  const lines = dotLines(odd, figure([]));
  assert.match(line(lines, "&quot;hi&quot;"), /Say &quot;hi&quot; &lt;b&gt;/);
  assert.match(line(lines, "A &amp; B"), /A &amp; B/);
  assert.match(line(lines, "now"), /label="it says \\"go\\" \\\\ now"/);
});

const graphviz = spawnSync("dot", ["-V"]).status === 0;

test("Graphviz draws backslashes and newlines in labels as written", { skip: !graphviz && "no dot" }, () => {
  const odd = chart(
    [state(["C:\\Names"], { start: true }), state(["two\nlines"])],
    [step(["C:\\Names"], ["two\nlines"], "go \\G")],
  );
  const moves = [{ n: 1, region: 0, from: ["C:\\Names"], to: ["two\nlines"], label: "go \\G" }];
  const svg = spawnSync("dot", ["-Tsvg"], { input: dotLines(odd, figure(moves, [["C:\\Names"]])).join("\n") }).stdout.toString();
  const texts = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
  assert.ok(texts.includes("C:\\Names"), texts.join(" | "));
  assert.ok(texts.includes("two") && texts.includes("lines"), texts.join(" | "));
  assert.ok(texts.includes("① go \\G"), texts.join(" | "));
});

test("a lifted exit beats a sibling with the same target, and gets the number", () => {
  const region: Region = {
    name: "",
    states: [state(["Trying", "First"], { start: true }), state(["Trying", "Last"]), state(["Out"])],
    steps: [step(["Trying"], ["Out"], "you give up"), step(["Trying", "Last"], ["Out"], "you are locked out")],
    arrows: [],
  };
  const give = { n: 1, region: 0, from: ["Trying", "Last"], to: ["Out"], label: "you give up" };
  assert.equal(stepOf(region, give)?.label, "you give up");
  const locked = { ...give, label: "you are locked out" };
  assert.deepEqual(stepOf(region, locked)?.from, ["Trying", "Last"]);
  const merged: Region = { ...region, steps: [step(["Trying", "Last"], ["Out"], "a or b"), step(["Trying"], ["Out"], "a or b or c")] };
  assert.deepEqual(stepOf(merged, { ...give, label: "a or b" })?.from, ["Trying", "Last"]);
  assert.deepEqual(stepOf(merged, { ...give, label: "c" })?.from, ["Trying"]);
  const lines = dotLines({ ...chart([], []), regions: [region] }, figure([give]));
  assert.match(line(lines, "you give up"), /① you give up.*ltail=cluster_\d+/);
});

test("a path step matches the arrow from its own source, not a same-named twin", () => {
  const region: Region = {
    name: "",
    states: [state(["A"], { start: true }), state(["B"]), state(["Out"])],
    steps: [step(["A"], ["Out"], "you leave"), step(["B"], ["Out"], "you leave")],
    arrows: [],
  };
  assert.deepEqual(stepOf(region, { n: 1, region: 0, from: ["B"], to: ["Out"], label: "you leave" })?.from, ["B"]);
});

test("depth walks through a parent's exit, so the way back from it does not rank", () => {
  const nested = chart(
    [state(["Start"], { start: true }), state(["Trying", "First"]), state(["Out"])],
    [
      step(["Start"], ["Trying", "First"], "you begin"),
      step(["Trying"], ["Out"], "you give up"),
      step(["Out"], ["Start"], "you start again"),
    ],
  );
  assert.match(line(dotLines(nested, figure([])), "you start again"), /constraint=false/);
});

test("sibling parent boxes close before the next one opens", () => {
  const two = chart(
    [state(["A", "a1"], { start: true }), state(["B", "b1"]), state(["A", "a2"])],
    [step(["A", "a1"], ["B", "b1"], "x"), step(["B", "b1"], ["A", "a2"], "y")],
  );
  const lines = dotLines(two, figure([]));
  const a = lines.findIndex((l) => l.includes('label="A"'));
  const b = lines.findIndex((l) => l.includes('label="B"'));
  const between = lines.slice(a, b);
  assert.ok(between.some((l) => l.includes("a1")) && between.some((l) => l.includes("a2")), "A holds a1 and a2");
  assert.ok(between.some((l) => l.trim() === "}"), "A closes before B opens");
  assert.equal(lines.filter((l) => l.trim() === "}").length, 3, "two boxes and the graph");
});

// End to end through TLC, when the tla-tools jar is installed.
const jar = process.env.TLA2TOOLS ?? "/usr/share/java/tla2tools.jar";
const tlc = fs.existsSync(jar) && graphviz;
const launcher = path.join(import.meta.dirname, "..", "statechart");
const run = (files: { [name: string]: string }, args: string[]) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "statechart-test-"));
  for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, name), text);
  const r = spawnSync(launcher, [...args, "--out", "out"], { cwd: dir, env: { ...process.env, TLA2TOOLS: jar } });
  const read = (name: string) => fs.readFileSync(path.join(dir, "out", name), "utf8");
  return { status: r.status, stderr: r.stderr.toString(), read, files: () => fs.readdirSync(path.join(dir, "out")), dir };
};

test("numbers the shortest path in each region it moves", { skip: !tlc && "no TLC or Graphviz" }, () => {
  const r = run(
    {
      "Room.tla": [
        "---- MODULE Room ----",
        "VARIABLES light, door",
        "Init == light = \"off\" /\\ door = \"closed\"",
        "Switch == light = \"off\" /\\ light' = \"on\" /\\ UNCHANGED door",
        "Open == door = \"closed\" /\\ door' = \"open\" /\\ UNCHANGED light",
        "Next == Switch \\/ Open",
        "NotOpenAndOn == ~(door = \"open\" /\\ light = \"on\")",
        "====",
      ].join("\n"),
      "Room.cfg": "INIT Init\nNEXT Next\nINVARIANT NotOpenAndOn\nCHECK_DEADLOCK FALSE\n",
      "view.mjs": [
        "export const state = (v) => ({ Light: v.light, Door: v.door });",
        "export const step = (a) => ({ Switch: 'you switch it on', Open: 'you open the door' })[a];",
      ].join("\n"),
    },
    ["Room.tla", "--view", "view.mjs"],
  );
  assert.equal(r.status, 1, r.stderr);
  const dot = r.read("chart-NotOpenAndOn.dot");
  assert.match(dot, /label="Light"; class="region"/);
  assert.match(dot, /label="Door"; class="region"/);
  // Either order is a shortest path; the two numbers must sit on the two regions' steps.
  const numbered = [...dot.matchAll(/label=<<B>([①-⑳]) ([^<]*)<\/B>>/g)].map((m) => m[2]).sort();
  assert.deepEqual(numbered, ["you open the door", "you switch it on"], dot);
  assert.deepEqual([...dot.matchAll(/<B>([①-⑳]) /g)].map((m) => m[1]).sort(), ["①", "②"]);
  assert.equal(spawnSync("dot", ["-Tsvg"], { input: dot }).status, 0);
});

test("a run stuck in one state says it stays there", { skip: !tlc && "no TLC or Graphviz" }, () => {
  const r = run(
    {
      "Shop.tla": [
        "---- MODULE Shop ----",
        "VARIABLES phase",
        "Init == phase = \"cart\"",
        "Pay == phase = \"cart\" /\\ phase' = \"paid\"",
        "Done == phase = \"paid\" /\\ UNCHANGED phase",
        "Next == Pay \\/ Done",
        "Spec == Init /\\ [][Next]_phase",
        "EventuallyPaid == <>(phase = \"paid\")",
        "====",
      ].join("\n"),
      "Shop.cfg": "SPECIFICATION Spec\nPROPERTY EventuallyPaid\n",
    },
    ["Shop.tla"],
  );
  assert.equal(r.status, 1, r.stderr);
  const dot = r.read("chart-EventuallyPaid.dot");
  assert.match(dot, /↻ stays here forever/);
});
