// bun test test/intent.test.js   (from the module directory)
import { test, expect } from "bun:test";
import { readFileSync } from "fs";

const src = readFileSync(new URL("../home/.config/quickshell/dock/Intent.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const I = new Function(src + "\nreturn { defaults, within, dist, enterCommit, openStep, headingToRow, homeState, closeStep, sustainedVelocity, waveSlipVx, uFromX, posOfU, rayRect, unionExit, smooth01 };")();
const cfg = I.defaults(0.35, 0.45);
const dt = 1 / 144;

// Folder icon magnified to 128 px with its bottom at window y 661 (as in the dock).
const icon = { x0: 636, x1: 764, y0: 533, y1: 661 };
const funnel = { x0: 530, x1: 870, y0: 257, y1: 661 };     // pre-open finite funnel (340 px wide)
const colToEdge = { x0: 636, x1: 764, y0: 147, y1: 680 };   // closing: panel down to screen edge
const panel = { x0: 550, x1: 850, y0: 147, y1: 517 };

// Simulates open progress under constant velocity `v`. `engaged` models whether rise-engage
// (funnel hold + upward boost) has unlocked.
function runOpen(p0, v, secs, engaged = false) {
    let P = 0, peak = 0, started = null, latched = null, touched = false;
    for (let t = 0; t < secs; t += dt) {
        const p = { x: p0.x + v.x * t, y: p0.y + v.y * t };
        touched = touched || I.within(p, icon);
        const activeEngage = engaged && touched;
        const holds = activeEngage ? (P > cfg.openStart ? [funnel, panel] : [funnel]) : [];
        const c = I.enterCommit(p, v, icon, holds, activeEngage, cfg);
        P = I.openStep(P, c, v.y, dt, cfg, activeEngage);
        peak = Math.max(peak, P);
        if (started === null && P >= cfg.openStart) started = t;
        if (latched === null && P >= cfg.latch) latched = t;
    }
    return { peak, started, latched };
}

test("sideways sweeps and slow glides (including 8 px/s) never reach openStart", () => {
    // Both window-space (with ~3.6x wave slide) and bare icon-relative speeds >= 8 px/s stay below openStart.
    for (const rel of [8, 12, 28.6, 40, 65, 150, 600, 2500]) {
        const r = runOpen({ x: 600, y: 600 }, { x: rel, y: 0 }, 240 / rel);
        expect(r.started).toBeNull();
        expect(r.latched).toBeNull();
        expect(r.peak).toBeLessThan(cfg.openStart);
    }
});

test("plain vertical exit without rise-engage never opens or reaches openStart", () => {
    for (const up of [600, 1200, 2000]) {
        const r = runOpen({ x: 700, y: 620 }, { x: 0, y: -up }, 0.6, false);
        expect(r.started).toBeNull();
        expect(r.latched).toBeNull();
        expect(r.peak).toBeLessThan(0.25);
    }
});

test("rising from an engaged folder opens fast at 0 to 30 degrees (window coordinates, wave frozen)", () => {
    for (const deg of [0, 8, 15, 20, 30]) {
        const rad = deg * Math.PI / 180;
        const v = { x: 600 * Math.sin(rad), y: -600 * Math.cos(rad) };
        const r = runOpen({ x: 700, y: 620 }, v, 0.35, true);
        expect(r.latched).not.toBeNull();
        expect(r.latched).toBeLessThan(0.22);
    }
});

test("a trajectory heading into the folder icon starts before arrival, moving away does not", () => {
    const c = I.enterCommit({ x: 700, y: 678 }, { x: 0, y: -400 }, icon, [], false, cfg);
    expect(c).toBeGreaterThan(0.35);
    const away = I.enterCommit({ x: 700, y: 678 }, { x: 0, y: 400 }, icon, [], false, cfg);
    expect(away).toBe(0);
});

test("the funnel hold only counts once engaged", () => {
    const p = { x: 700, y: 400 };
    expect(I.enterCommit(p, { x: 0, y: 0 }, icon, [funnel], false, cfg)).toBe(0);
    expect(I.enterCommit(p, { x: 0, y: 0 }, icon, [funnel], true, cfg)).toBeGreaterThan(0.99);
});

test("headingToRow detects trajectories descending onto the bar and rejects sideways/misses/still", () => {
    const row = { x0: 320, x1: 1080, y0: 624, y1: 680 };
    expect(I.headingToRow({ x: 700, y: 420 }, { x: -240, y: 180 }, row, cfg.rowHorizon)).toBe(true);
    expect(I.headingToRow({ x: 700, y: 420 }, { x: 1200, y: 900 }, row, cfg.rowHorizon)).toBe(true);
    expect(I.headingToRow({ x: 700, y: 420 }, { x: -800, y: 150 }, row, cfg.rowHorizon)).toBe(false); // misses left
    expect(I.headingToRow({ x: 700, y: 420 }, { x: -400, y: 0 }, row, cfg.rowHorizon)).toBe(false);   // sideways
    expect(I.headingToRow({ x: 700, y: 420 }, { x: 0, y: -400 }, row, cfg.rowHorizon)).toBe(false);   // upward
    expect(I.headingToRow({ x: 700, y: 420 }, { x: 0, y: 0 }, row, cfg.rowHorizon)).toBe(false);      // still
    expect(I.headingToRow({ x: 700, y: 200 }, { x: 0, y: 50 }, row, cfg.rowHorizon)).toBe(false);     // beyond horizon
});

// close
const home = [colToEdge, panel];
function runClose(p0, v, secs, C = 0) {
    let prevD = null;
    for (let t = 0; t < secs; t += dt) {
        const p = { x: p0.x + v.x * t, y: p0.y + v.y * t };
        const s = I.homeState(p, v, home);
        const recede = prevD === null ? 0 : (s.dist - prevD) / dt;
        prevD = s.dist;
        C = I.closeStep(C, dt, s, recede, cfg);
        if (C >= 1) return t;
    }
    return null;
}

test("decisive departure closes far faster than resting just outside", () => {
    const tRest = runClose({ x: 862, y: 300 }, { x: 0, y: 0 }, 2);
    const tFast = runClose({ x: 852, y: 300 }, { x: 1500, y: -300 }, 1);
    expect(tRest).toBeGreaterThan(0.32);
    expect(tRest).toBeLessThan(0.45);
    expect(tFast).toBeLessThan(0.13);
});

test("genuine returns within returnHorizon drain closeI instead of closing", () => {
    expect(runClose({ x: 1020, y: 300 }, { x: -400, y: 0 }, 0.4)).toBeNull();
    expect(runClose({ x: 1010, y: 300 }, { x: -200, y: 0 }, 0.75, 0.8)).toBeNull();
    expect(runClose({ x: 950, y: 300 }, { x: -100, y: 0 }, 1.2, 0.96)).toBeNull();
});

test("creeping slowly toward the panel from beyond returnHorizon still closes (R11)", () => {
    // 200 px right of panel (x1 = 850) creeping at 20 px/s -> eta = 10 s >> returnHorizon (1.5 s)
    const t = runClose({ x: 1050, y: 300 }, { x: -20, y: 0 }, 2, 0.5);
    expect(t).not.toBeNull();
    expect(t).toBeLessThan(0.2);
});

test("uFromX and posOfU are exact inverses across interior, gaps, and past-end regions", () => {
    const lefts = [100, 146, 300], sizes = [38, 146, 30], gap = 8, pitch = 38;
    for (const x of [40, 96, 119, 142, 219, 296, 315, 372]) {
        const u = I.uFromX(x, lefts, sizes, gap, pitch);
        expect(I.posOfU(u, lefts, sizes, gap, pitch)).toBeCloseTo(x, 6);
    }
    expect(I.uFromX(100 - 4, lefts, sizes, gap, pitch)).toBeCloseTo(0);
    expect(I.uFromX(146 - 4 + 77, lefts, sizes, gap, pitch)).toBeCloseTo(1.5);
    expect(I.uFromX(300 + 30 + 4 + 38, lefts, sizes, gap, pitch)).toBeCloseTo(4);
});

test("sustainedVelocity preserves steady glides, brakes to 0 in wShort, and rejects oscillatory tremor", () => {
    const stepDt = 1 / 240;
    // Steady 8 px/s glide
    const hGlide = [];
    for (let t = 0; t <= 0.5; t += stepDt) hGlide.push({ t, x: 8 * t });
    expect(I.sustainedVelocity(hGlide, 0.5, 4.0, 0.05, 0.20)).toBeCloseTo(8, 1);

    // Braking from 600 px/s to dead stop: hits 0 within 60 ms
    const hBrake = [];
    for (let t = 0; t <= 0.36; t += stepDt) hBrake.push({ t, x: t <= 0.3 ? 600 * t : 180 });
    expect(Math.abs(I.sustainedVelocity(hBrake, 0.36, 180, 0.05, 0.20))).toBeLessThan(0.5);

    // 2 px @ 3 Hz tremor stays low on average (cancels across reversals)
    const hTremor = [];
    let sum = 0, count = 0;
    for (let t = 0; t <= 1.5; t += stepDt) {
        const x = 2 * Math.sin(2 * Math.PI * 3 * t);
        hTremor.push({ t, x });
        while (hTremor.length > 2 && hTremor[0].t < t - 0.28) hTremor.shift();
        if (t > 0.28) { sum += Math.abs(I.sustainedVelocity(hTremor, t, x, 0.05, 0.25)); count++; }
    }
    expect(sum / count).toBeLessThan(3);
});

test("waveSlipVx keeps opposing wave slip on glides and ignores wave-settle lag when stopped", () => {
    expect(I.waveSlipVx(8, -20.8)).toBeCloseTo(28.8, 3);
    expect(I.waveSlipVx(0, -180)).toBe(0);
    expect(I.waveSlipVx(5, -200)).toBeCloseTo(18, 3);
});

test("closeStep appCloseMult accelerates closing when directly on an app icon", () => {
    const s = { inside: false, dist: 20, eta: Infinity, recede: 0 };
    const normal = I.closeStep(0, 0.05, s, 0, cfg, 1);
    const onApp = I.closeStep(0, 0.05, s, 0, cfg, cfg.appCloseMult);
    expect(onApp / normal).toBeCloseTo(cfg.appCloseMult, 5);
});
