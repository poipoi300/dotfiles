// bun test test/dock.test.js   (from the module directory)
// Lifts the real function bodies verbatim from Dock.qml and drives them with the real Intent.js
// and config.json at 144 Hz, covering the state machine, wave layout, hiding, and R1-R14.
import { test, expect } from "bun:test";
import { readFileSync } from "fs";

const dockSrc = readFileSync(new URL("../home/.config/quickshell/dock/Dock.qml", import.meta.url), "utf8");
const intentSrc = readFileSync(new URL("../home/.config/quickshell/dock/Intent.js", import.meta.url), "utf8").replace(/^\.pragma library\s*$/m, "");
const config = JSON.parse(readFileSync(new URL("../home/.config/quickshell/dock/config.json", import.meta.url), "utf8"));

const Intent = new Function(
    intentSrc + "\nreturn { defaults, within, dist, slab, rayRect, unionExit, enterCommit, approach, openStep, headingToRow, homeState, closeStep, sustainedVelocity, waveSlipVx, uFromX, posOfU, smooth01 };"
)();

// Extract a top-level `    function name(...) { ... }` block verbatim from Dock.qml.
function extractFn(src, name) {
    const marker = new RegExp(`^    function ${name}\\b`, "m");
    const m = marker.exec(src);
    if (!m) throw new Error(`Function ${name} not found in Dock.qml`);
    const start = m.index;
    const braceStart = src.indexOf("{", start);
    let depth = 0, inStr = null, inLineComment = false;
    for (let i = braceStart; i < src.length; i++) {
        const ch = src[i];
        if (inLineComment) { if (ch === "\n") inLineComment = false; continue; }
        if (inStr) {
            if (ch === "\\") { i++; continue; }
            if (ch === inStr) inStr = null;
            continue;
        }
        if (ch === "/" && src[i + 1] === "/") { inLineComment = true; i++; continue; }
        if (ch === '"' || ch === "'" || ch === "`") { inStr = ch; continue; }
        if (ch === "{") depth++;
        else if (ch === "}") {
            depth--;
            if (depth === 0) return src.slice(start, i + 1);
        }
    }
    throw new Error(`Unclosed function ${name}`);
}

const FN_NAMES = [
    "waveWeight", "waveAt", "foldMag", "relayout", "restCentre",
    "iconRect", "columnRect", "funnelRect", "panelRect", "rowRect", "onIcon", "onRow", "inZone",
    "tick", "updateIntent", "pointerAt", "revealTriggered", "regionGrew", "pointerLeft",
    "show", "hide", "dockGone", "panelGone",
    "isFolder", "loadPanel", "latch", "pin", "switchTo", "openFolder", "closeFolder"
];
const liftedSource = FN_NAMES.map(n => extractFn(dockSrc, n)).join("\n\n");

// Compile the lifted functions inside a `with (win)` scope so they run identically to Dock.qml.
const bindDockFns = new Function("win", "Intent", "Date", `
    with (win) {
        ${liftedSource}
        return { ${FN_NAMES.join(", ")} };
    }
`);

function createDock() {
    let nowMs = 10000;
    const VDate = { now: () => nowMs };

    const items = config.items;
    const base = config.icon.size;
    const peak = config.icon.peak;
    const gap = config.icon.spacing;
    const pitch = base + gap;
    const n = items.length;
    const restW = Math.max(0, n * pitch - gap);
    const waveSlots = Math.max(1, config.icon.waveSlots);
    const barH = 48, bottomMargin = 8, padX = 12, winH = 680;
    const barBottom = winH - bottomMargin;
    const iconBottom = barBottom - 11;
    const barZone = bottomMargin + barH + 8;
    const fadeStart = peak / 2 + 1.5 * gap;
    const fadeLen = 0.75 * peak;
    const foldDist = barH + bottomMargin + 14;

    const waveWeight0 = d => d >= waveSlots ? 0 : Math.pow(0.5 * (1 + Math.cos(Math.PI * d / waveSlots)), 1.25);
    let maxSide = 0;
    for (let c = 0; c <= 1; c += 0.05) {
        let g = 0;
        for (let k = 0; k <= waveSlots + 1; k++) {
            const d = c + k;
            g += k === 0 ? c * waveWeight0(Math.abs(c - 0.5)) : waveWeight0(d - 0.5);
        }
        maxSide = Math.max(maxSide, g);
    }
    maxSide *= (peak - base);
    const zoneExt = Math.max(maxSide * 1.5 + padX, fadeStart + fadeLen - base / 2) + 16;
    const winW = Math.max(1400, Math.ceil(restW + 2 * zoneExt + 64));
    const restLeft = (winW - restW) / 2;

    const makeAnim = (prop, onDone) => ({
        running: false, from: 0, to: 0, duration: 0, t0: 0, easing: { type: "OutCubic" },
        start() { this.running = true; this.t0 = nowMs; },
        stop() { this.running = false; },
        step() {
            if (!this.running) return;
            const u = this.duration > 0 ? Math.min(1, (nowMs - this.t0) / this.duration) : 1;
            const e = this.easing.type === "InQuad" ? u * u
                    : this.easing.type === "OutQuad" ? 1 - (1 - u) * (1 - u)
                    : 1 - Math.pow(1 - u, 3);
            win[prop] = this.from + (this.to - this.from) * e;
            if (u >= 1) { this.running = false; onDone(); }
        }
    });

    const makeTimer = (cb) => ({
        running: false, interval: 0, fireAt: 0,
        restart() { this.running = true; this.fireAt = nowMs + this.interval; },
        stop() { this.running = false; },
        step() {
            if (this.running && nowMs >= this.fireAt) { this.running = false; cb(); }
        }
    });

    let _folderIndex = -1;
    let _folderOpen = false;
    let _active = false;
    let _panelMaskOn = false;

    const baseCfg = Intent.defaults(config.behavior.folderHoverDelayMs / 1000, config.behavior.folderLeaveDelayMs / 1000);
    const win = {
        winW, winH, barH, bottomMargin, barBottom, iconBottom, padX,
        items, n, base, peak, gap, pitch, restW, restLeft, barZone, waveSlots,
        fadeStart, fadeLen, maxSide, zoneExt, foldDist,
        cfg: baseCfg,
        switchCfg: Object.assign({}, baseCfg, { sideSpeed: Infinity, openTau: baseCfg.openTau * baseCfg.switchTauFactor }),
        suppressed: false,
        magnifying: false,
        sim: false,
        px: winW / 2,
        pxTarget: winW / 2,
        sizes: [],
        lefts: [],
        hoverIndex: -1,
        magT: 0,
        hoverT: 0,
        ptrHover: { hovered: false },
        ptrOnBackdrop: false,
        ptrX: winW / 2,
        ptrY: winH,
        lastX: winW / 2,
        lastY: winH,
        vX: 0,
        vY: 0,
        simClock: 0,
        ptrHist: [],
        sustVx: 0,
        centers: [],
        iconV: [],
        folderTouched: false,
        touchSlowDwell: 0,
        iconIdle: 0,
        peakUpSpeed: 0,
        riseEngaged: false,
        opening: false,
        panelAnchorIndex: -1,
        clickOpened: false,
        openP: 0,
        switchIndex: -1,
        switchP: 0,
        closeI: 0,
        recede: 0,
        dismissed: -1,
        navHold: null,
        panelG: 0,
        centerW: 0,
        anchorX: winW / 2,
        pinIndex: -1,
        pinX: 0,
        closeShift: 0,
        lastPs: 0,
        dockY: foldDist,
        foldStartY: 0,
        dockHidden: true,
        dropMode: "fold",
        panelDropping: false,
        panelY: 0,
        regionAt: 0,
        regionGraceMs: 50,
        Easing: { OutCubic: "OutCubic", OutQuad: "OutQuad", InQuad: "InQuad" },
        Items: { behavior: Object.assign({}, config.behavior), checkDirs() {} },
        smoother: { running: false, start() { this.running = true; }, stop() { this.running = false; } },
        folderPanel: {
            root: null, stack: [], anchorCx: winW / 2,
            bottomY: iconBottom - peak - 16,
            width: 316, height: 370, // typical 3x3 dir folder (12 entries -> 4x3 = 412x370 or 316x370)
            get x() { return Math.max(8, Math.min(this.anchorCx - this.width / 2, winW - this.width - 8)); },
            get y() { return this.bottomY - this.height; },
            openNode(node) {
                this.root = node;
                this.stack = [node];
                const cnt = node.type === "group" ? node.items.length : 12;
                const cols = cnt <= 4 ? Math.max(cnt, 2) : cnt <= 9 ? 3 : cnt <= 16 ? 4 : 5;
                const rows = Math.min(4, Math.max(1, Math.ceil(cnt / cols)));
                this.width = cols * 96 + 28;
                this.height = 44 + rows * 104 + 14;
            },
            clear() { if (win.panelG <= 0.01) { this.root = null; this.stack = []; } },
            fadeIn() {}
        },
        bar: {
            get x0() { return win.n && win.lefts.length ? win.lefts[0] : win.restLeft; },
            get x1() { return win.n && win.lefts.length ? win.lefts[win.n - 1] + win.sizes[win.n - 1] : win.restLeft + win.restW; },
            get x() { return this.x0 - win.padX; },
            get width() { return this.x1 - this.x0 + 2 * win.padX; }
        },
        zone: {
            get hiddenW() { return win.restW + 80; },
            get shownW() { return win.restW + 2 * win.zoneExt; },
            get tall() { return win.barZone + win.peak + 40; },
            get width() { return win.suppressed ? 0 : (win.active ? this.shownW : this.hiddenW); },
            get height() { return win.suppressed ? 0 : (win.active ? this.tall : 4); },
            get x() { return (win.winW - this.width) / 2; },
            get y() { return win.winH - this.height; }
        },
        get panelMaskOn() {
            return !win.panelDropping && (win.folderOpen || win.opening || win.panelG > 0.05
                || (win.folderIndex >= 0 && win.riseEngaged && win.openP > 0.20 && win.vY < -30));
        },
        get ptrInWin() { return win.ptrHover.hovered || win.sim; },
        get wantMag() { return win.active && win.magnifying && win.ptrInWin; },
        get hoverTarget() { return (win.folderOpen || win.opening || win.panelG > 0.05 || win.wantMag) ? 1 : 0; },
        get active() { return _active; },
        set active(v) {
            const was = _active;
            _active = v;
            if (!was && v) win.regionGrew(); // zone.onHeightChanged when win.active
        },
        get folderOpen() { return _folderOpen; },
        set folderOpen(v) {
            if (_folderOpen === v) return;
            _folderOpen = v;
            if (!v) win.ptrOnBackdrop = false;
        },
        get folderIndex() { return _folderIndex; },
        set folderIndex(v) {
            if (_folderIndex === v) return;
            _folderIndex = v;
            win.folderTouched = false;
            win.touchSlowDwell = 0;
            win.iconIdle = 0;
            win.peakUpSpeed = 0;
            win.riseEngaged = false;
            if (v >= 0) win.panelAnchorIndex = v;
            else if (!win.folderOpen && !win.opening && win.panelG < 0.01) win.folderPanel.clear();
        }
    };

    win.dockAnim = makeAnim("dockY", () => { if (!win.active) win.dockGone(); });
    win.panelAnim = makeAnim("panelY", () => win.panelGone());
    win.revealTimer = makeTimer(() => win.revealTriggered());
    win.revealTimer.interval = Math.max(0, config.behavior.revealDelayMs || 0);
    win.regionGrace = makeTimer(() => win.pointerLeft());
    win.lostGrace = makeTimer(() => {
        if (win.folderOpen && !win.ptrHover.hovered && !win.sim && !win.ptrOnBackdrop)
            win.closeFolder("unit");
    });

    const fns = bindDockFns(win, Intent, VDate);
    Object.assign(win, fns);
    win.relayout();

    function syncMaskGrowth() {
        const nowOn = win.panelMaskOn;
        if (nowOn && !_panelMaskOn) win.regionGrew();
        _panelMaskOn = nowOn;
    }

    function inMask(x, y) {
        if (x >= win.zone.x && x <= win.zone.x + win.zone.width && y >= win.zone.y && y <= win.winH) return true;
        if (win.panelMaskOn) {
            const fp = win.folderPanel;
            if (x >= fp.x && x <= fp.x + fp.width && y >= fp.y && y <= fp.y + fp.height) return true;
        }
        return false;
    }

    // Drive real Wayland-like pointer delivery (mask + backdrop) at position (x, y).
    function movePtr(x, y) {
        const hitWin = inMask(x, y);
        const wasWin = win.ptrHover.hovered;
        const wasBack = win.ptrOnBackdrop;
        if (hitWin) {
            win.ptrHover.hovered = true;
            win.ptrOnBackdrop = false;
            win.pointerAt(x, y, true);
        } else {
            win.ptrHover.hovered = false;
            if (win.folderOpen) {
                win.ptrOnBackdrop = true;
                win.pointerAt(x, y, false);
            } else {
                win.ptrOnBackdrop = false;
                if (wasWin || wasBack) win.pointerLeft();
            }
        }
        syncMaskGrowth();
    }

    const dt = 1 / 144;
    function stepFrame(ptrFn) {
        nowMs += dt * 1000;
        if (ptrFn) {
            const pt = ptrFn(nowMs / 1000);
            if (pt) movePtr(pt[0], pt[1]);
        }
        win.dockAnim.step();
        win.panelAnim.step();
        win.revealTimer.step();
        win.regionGrace.step();
        win.lostGrace.step();
        if (win.smoother.running) win.tick(dt);
        if (!win.folderOpen && win.ptrOnBackdrop) win.ptrOnBackdrop = false;
        syncMaskGrowth();
    }

    function runSecs(secs, ptrFn, onFrame) {
        const frames = Math.round(secs / dt);
        const t0 = nowMs / 1000;
        for (let f = 0; f < frames; f++) {
            stepFrame(ptrFn ? t => ptrFn(t - t0) : null);
            if (onFrame) onFrame(nowMs / 1000 - t0, win);
        }
    }

    function simulateIpc(x) {
        win.sim = true;
        if (!win.active) { win.ptrX = x; win.ptrY = win.iconBottom - 15; win.show(); }
        win.pointerAt(x, win.iconBottom - 15, true);
    }

    function benchIpc(hz, ms) {
        win.sim = true;
        if (!win.active) { win.ptrX = win.restLeft; win.ptrY = win.iconBottom - 15; win.show(); }
        win.magnifying = true;
        const t0 = nowMs;
        const interval = Math.max(1, Math.round(1000 / hz));
        let nextTick = t0;
        runSecs(ms / 1000 + 0.05, null, () => {
            if (nowMs >= nextTick && win.sim) {
                nextTick += interval;
                const t = (nowMs - t0) / ms;
                if (t >= 1) { win.sim = false; win.pointerLeft(); return; }
                const ph = (t * 4) % 2;
                const f = ph < 1 ? ph : 2 - ph;
                win.pointerAt(win.restLeft + f * win.restW, win.iconBottom - 15, true);
            }
        });
    }

    function reloadItems() {
        if (win.folderOpen || win.openP > 0 || win.opening || win.panelG > 0) win.closeFolder("auto");
        win.folderIndex = -1;
        win.dismissed = -1;
        win.closeShift = 0;
    }

    return { win, movePtr, stepFrame, runSecs, simulateIpc, benchIpc, reloadItems, dt, now: () => nowMs / 1000 };
}

// Helper: reveal dock at bottom edge x (holding past revealDelayMs), move up onto bar, settle.
function revealAt(d, x, y = 620) {
    d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.03, () => [x, 678]);
    d.runSecs(0.18, () => [x, y]);
}

// Helper: open folder 6 cleanly by resting on it.
function openFolder6(d) {
    const cx = d.win.restCentre(6);
    revealAt(d, cx, 620);
    d.runSecs(0.55, () => [cx, 620]);
    expect(d.win.folderOpen).toBe(true);
    expect(d.win.folderIndex).toBe(6);
    expect(d.win.panelG).toBeGreaterThan(0.98);
}

test("Decision 2 & R6: sideways glides at 5, 6, 8, 10, 15, 20..2500 px/s never open or show panel", () => {
    for (const speed of [5, 6, 8, 10, 15, 20, 65, 300, 1200, 2500]) {
        const d = createDock();
        const startX = d.win.restCentre(3); // app icon left of folders 4 and 6
        const dist = 200;
        revealAt(d, startX, 620);
        let peakG = 0, peakC = 0, everOpen = false, maskOn = false;
        d.runSecs(dist / speed, t => [startX + speed * t, 620], (_, w) => {
            peakG = Math.max(peakG, w.panelG);
            peakC = Math.max(peakC, w.centerW);
            if (w.folderOpen) everOpen = true;
            if (w.panelMaskOn) maskOn = true;
        });
        expect(everOpen).toBe(false);
        expect(peakG).toBe(0);
        expect(peakC).toBe(0);
        expect(maskOn).toBe(false);
    }
});

test("R6: resting or tiny-drift opens run in one unbroken smooth motion, never hanging halfway", () => {
    for (const drift of [0, 0.5, 1, 2, 3, 5, 8]) {
        const d = createDock();
        const f6 = d.win.restCentre(6);
        revealAt(d, f6 - 20, 620);
        // Settle onto f6 from rest
        d.runSecs(0.1, () => [f6, 620]);
        let hangFrames = 0, prevG = 0, nonMono = false;
        const speeds = [];
        d.runSecs(2.0, t => [f6 + drift * t, 620], (_, w) => {
            if (w.panelG > 0.05 && w.panelG < 0.95) hangFrames++;
            if (w.panelG < prevG - 1e-4 && w.opening) nonMono = true;
            if (w.panelG > 0.001 && prevG < 0.999) speeds.push((w.panelG - prevG) / d.dt);
            prevG = w.panelG;
        });
        expect(nonMono).toBe(false);
        // Either never grows, or finishes growth (5%..95%) in <= 0.18 s (never hangs 0.3..0.7 s!)
        expect(hangFrames * d.dt).toBeLessThan(0.18);
        if (drift === 0) {
            expect(d.win.folderOpen).toBe(true);
            // Single hump: no deep dip between two speed peaks
            const maxV = Math.max(...speeds);
            const peaks = [];
            for (let i = 1; i < speeds.length - 1; i++) {
                if (speeds[i] > speeds[i - 1] && speeds[i] >= speeds[i + 1] && speeds[i] > 0.1 * maxV)
                    peaks.push(i);
            }
            let dips = 0;
            for (let k = 0; k < peaks.length - 1; k++) {
                const seg = speeds.slice(peaks[k], peaks[k + 1] + 1);
                if (Math.min(...seg) < 0.6 * Math.min(speeds[peaks[k]], speeds[peaks[k + 1]])) dips++;
            }
            expect(dips).toBe(0);
            // Icon centre equals panel centre
            const iconCx = d.win.lefts[6] + d.win.sizes[6] / 2;
            const panelCx = d.win.folderPanel.x + d.win.folderPanel.width / 2;
            expect(Math.abs(iconCx - panelCx)).toBeLessThan(0.5);
        }
        if (drift >= 5) expect(d.win.folderOpen).toBe(false);
    }
});

test("R1: heading from open folder panel to dock app icons 2, 5, 9, 11 shrinks and keeps dock at 300-2400 px/s", () => {
    for (const targetIdx of [2, 5, 9, 11]) {
        for (const speed of [300, 600, 1200, 2400]) {
            const d = createDock();
            openFolder6(d);
            // Move into panel at (700, 420)
            d.runSecs(0.25, () => [700, 420]);
            expect(d.win.folderOpen).toBe(true);
            const tx = d.win.lefts[targetIdx] + d.win.sizes[targetIdx] / 2;
            const ty = 645;
            const dist = Math.hypot(tx - 700, ty - 420);
            const dur = dist / speed;
            d.runSecs(dur, t => {
                const u = Math.min(1, t / dur);
                return [700 + (tx - 700) * u, 420 + (ty - 420) * u];
            });
            d.runSecs(0.35, () => [tx, ty]);
            expect(d.win.active).toBe(true);
            expect(d.win.folderOpen).toBe(false);
            expect(d.win.dropMode).not.toBe("unit");
        }
    }
});

test("R1: leaving open panel away from the row (sideways, upward, or stopping above short icons) unit-drops", () => {
    const exits = [
        { name: "sideways left", p1: [200, 420], speed: 800 },
        { name: "upward", p1: [700, 80], speed: 800 },
        { name: "stop above short icon 2", p1: [420, 560], speed: 600 },
        { name: "miss bar bottom-left", p1: [120, 650], speed: 800 }
    ];
    for (const ex of exits) {
        const d = createDock();
        openFolder6(d);
        d.runSecs(0.2, () => [700, 420]);
        const dist = Math.hypot(ex.p1[0] - 700, ex.p1[1] - 420);
        const dur = dist / ex.speed;
        d.runSecs(dur, t => {
            const u = Math.min(1, t / dur);
            return [700 + (ex.p1[0] - 700) * u, 420 + (ex.p1[1] - 420) * u];
        });
        d.runSecs(0.8, () => ex.p1);
        expect(d.win.active).toBe(false);
        expect(d.win.dropMode).toBe("unit");
    }
});

test("diagonal exit from open folder closes it, keeps dock centred (no lateral slide), and zooms under cursor", () => {
    // 1. Move up into folder 6 from folder icon, then move down diagonally left and right onto non-folder areas
    for (const targetX of [450, 540, 950, 1010]) {
        const d = createDock();
        const f6 = d.win.restCentre(6);
        d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.03, () => [f6, 678]);
        d.runSecs(0.06, () => [f6, 640]);
        d.runSecs(0.35, t => [f6, 640 - 240 * Math.min(1, t / 0.35)]);
        d.runSecs(0.25, () => [f6, 400]);
        expect(d.win.folderOpen).toBe(true);
        const barXOpen = d.win.bar.x;
        let maxShift = 0;
        const dur = Math.hypot(targetX - f6, 645 - 400) / 800;
        d.runSecs(dur, t => {
            const u = Math.min(1, t / dur);
            return [f6 + (targetX - f6) * u, 400 + (645 - 400) * u];
        }, (_, w) => { maxShift = Math.max(maxShift, Math.abs(w.lastPs)); });
        d.runSecs(0.35, () => [targetX, 645], (_, w) => {
            maxShift = Math.max(maxShift, Math.abs(w.lastPs));
        });
        expect(d.win.active).toBe(true);
        expect(d.win.folderOpen).toBe(false);
        expect(maxShift).toBeLessThan(1);
        expect(Math.abs(d.win.px - targetX)).toBeLessThan(1);
        const expectedBarX = d.win.waveAt(targetX, 1).l[0] - d.win.padX;
        expect(Math.abs(d.win.bar.x - expectedBarX)).toBeLessThan(1);
        // Sweeping laterally from targetX across the dock keeps ps === 0 and tracks cursor monotonically
        const dir = targetX < 700 ? 1 : -1;
        const sweepEnd = targetX + dir * 320;
        let prevHover = d.win.hoverIndex;
        d.runSecs(0.5, t => [targetX + (sweepEnd - targetX) * (t / 0.5), 645], (_, w) => {
            expect(Math.abs(w.lastPs)).toBeLessThan(1);
            if (w.hoverIndex >= 0 && prevHover >= 0) {
                if (dir > 0) expect(w.hoverIndex).toBeGreaterThanOrEqual(prevHover);
                else expect(w.hoverIndex).toBeLessThanOrEqual(prevHover);
                prevHover = w.hoverIndex;
            }
        });
    }
    // Aiming at neighbour folder 7 switches directly to folder 7 and keeps it under the pointer
    const d7 = createDock();
    openFolder6(d7);
    d7.runSecs(0.2, () => [700, 420]);
    const tx7 = d7.win.lefts[7] + d7.win.sizes[7] / 2;
    const dur7 = Math.hypot(tx7 - 700, 645 - 420) / 800;
    d7.runSecs(dur7, t => {
        const u = Math.min(1, t / dur7);
        return [700 + (tx7 - 700) * u, 420 + (645 - 420) * u];
    });
    d7.runSecs(0.5, () => [tx7, 645]);
    expect(d7.win.active).toBe(true);
    expect(d7.win.folderOpen).toBe(true);
    expect(d7.win.folderIndex).toBe(7);
    expect(d7.win.hoverIndex).toBe(7);
});

test("R3: edge flick leaving during regionGrace holds magnification (no settle step before fold)", () => {
    const d = createDock();
    const x = d.win.restCentre(2); // app icon
    // Touch edge long enough to reveal, immediately rise into bar for 20 ms (within regionGraceMs=50), then flick straight out
    d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.01, () => [x, 678]);
    d.runSecs(0.02, () => [x, 635]);
    const magBeforeLeave = d.win.hoverT;
    expect(magBeforeLeave).toBeGreaterThan(0.2);
    let minMagWhileActive = magBeforeLeave;
    d.runSecs(0.10, t => [x, Math.max(0, 635 - 4000 * t)], (_, w) => {
        if (w.active) minMagWhileActive = Math.min(minMagWhileActive, w.hoverT);
    });
    // Must NOT have deflated in place while waiting in regionGrace
    expect(minMagWhileActive).toBeGreaterThanOrEqual(magBeforeLeave - 0.01);
    // And must fold cleanly once regionGrace expires
    d.runSecs(0.3, () => [x, 0]);
    expect(d.win.active).toBe(false);
    expect(d.win.dockHidden).toBe(true);
});

test("R4: sliding onto folder 6 and exiting straight up at 600, 1200, 2000 px/s never opens or flashes panel", () => {
    for (const upSpeed of [600, 1200, 2000]) {
        const d = createDock();
        const f6 = d.win.restCentre(6);
        const startX = f6 - 140;
        revealAt(d, startX, 620);
        // Slide sideways onto folder 6 at 500 px/s
        const slideDur = 140 / 500;
        d.runSecs(slideDur, t => [startX + 500 * t, 620]);
        // Immediately exit vertically upward all the way off the dock
        let peakG = 0, everOpen = false;
        d.runSecs(0.6, t => [f6, Math.max(-50, 620 - upSpeed * t)], (_, w) => {
            peakG = Math.max(peakG, w.panelG);
            if (w.folderOpen) everOpen = true;
        });
        expect(everOpen).toBe(false);
        expect(peakG).toBe(0);
        expect(d.win.active).toBe(false);
    }
});

test("R5: rising diagonally (0 to 30 degrees, window coords) from folder 6 opens cleanly", () => {
    // Subcase A: brief touch dwell on folder 6 (60 ms) then diagonal rise into panel at 400 & 600 px/s
    for (const speed of [400, 600]) {
        for (const deg of [0, 8, 15, 20, 30]) {
            const d = createDock();
            const f6 = d.win.restCentre(6);
            d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.02, () => [f6, 678]);
            d.runSecs(0.06, () => [f6, 640]);
            const rad = deg * Math.PI / 180;
            const vx = speed * Math.sin(rad), vy = -speed * Math.cos(rad);
            const dur = 200 / (-vy);
            d.runSecs(dur, t => [f6 + vx * t, 640 + vy * t]);
            d.runSecs(0.3, () => [f6 + vx * dur, 440]);
            expect(d.win.folderOpen).toBe(true);
            expect(d.win.folderIndex).toBe(6);
        }
    }
    // Subcase B: once revealed at bottom edge, smooth minimum-jerk rise directly into panel (y=410) at 0..30 deg
    for (const deg of [0, 8, 15, 20, 30]) {
        const d = createDock();
        const f6 = d.win.restCentre(6);
        d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.02, () => [f6, 679]);
        const dy = 679 - 410;
        const dx = dy * Math.tan(deg * Math.PI / 180);
        const dur = 0.5;
        d.runSecs(dur, t => {
            const u = Math.min(1, t / dur);
            const s = u * u * u * (10 + u * (-15 + 6 * u));
            return [f6 + dx * s, 679 - dy * s];
        });
        d.runSecs(0.4, () => [f6 + dx, 410]);
        expect(d.win.folderOpen).toBe(true);
        expect(d.win.folderIndex).toBe(6);
    }
});

test("R7: simulate and bench IPC hooks reveal a hidden dock and magnify icons", () => {
    const d = createDock();
    expect(d.win.active).toBe(false);
    d.simulateIpc(d.win.restCentre(6));
    expect(d.win.active).toBe(true);
    expect(d.win.magnifying).toBe(true);
    expect(d.win.vX).toBe(0);
    expect(d.win.vY).toBe(0);
    d.runSecs(0.25);
    expect(d.win.sizes[6]).toBeGreaterThan(100);

    const d2 = createDock();
    let maxSize = 0;
    d2.benchIpc(60, 400);
    // Re-check with frame spy
    const d3 = createDock();
    d3.win.sim = true;
    d3.win.ptrX = d3.win.restLeft; d3.win.ptrY = d3.win.iconBottom - 15;
    d3.win.show();
    d3.win.magnifying = true;
    d3.runSecs(0.3, null, (t, w) => {
        w.pointerAt(w.restLeft + Math.min(1, t / 0.3) * w.restW, w.iconBottom - 15, true);
        maxSize = Math.max(maxSize, ...w.sizes);
    });
    expect(maxSize).toBeGreaterThan(100);
});

test("R8: IPC open 4 and open 8 do not close in 3 frames, and switchTo does not spike recede", () => {
    for (const fi of [4, 8]) {
        const d = createDock();
        d.win.openFolder(fi, true);
        expect(d.win.folderOpen).toBe(true);
        // After 5 frames (~50 ms), must still be open with zero recede spike!
        d.runSecs(0.05);
        expect(d.win.folderOpen).toBe(true);
        expect(Math.abs(d.win.recede)).toBeLessThan(1);
        // Stays open past 0.20 s (no 3-frame spike close; closes smoothly after ~0.24 s with recede=0)
        d.runSecs(0.15);
        expect(d.win.folderOpen).toBe(true);
        expect(Math.abs(d.win.recede)).toBeLessThan(1);
    }
    // switchTo from 4 to 6 has zero recede spike
    const d = createDock();
    const f4 = d.win.restCentre(4);
    revealAt(d, f4, 620);
    d.win.openFolder(4, true);
    d.runSecs(0.2, () => [f4, 620]);
    const f6x = d.win.lefts[6] + d.win.sizes[6] / 2;
    d.win.switchTo(6);
    d.stepFrame(() => [f6x, 620]);
    expect(Math.abs(d.win.recede)).toBeLessThan(5);
});

test("R12: re-entering bottom strip mid-fold produces zero false velocity spike", () => {
    const d = createDock();
    const x1 = d.win.restCentre(10);
    revealAt(d, x1, 620);
    // Leave out the side past regionGrace
    d.runSecs(0.05, () => [1380, 300]);
    expect(d.win.active).toBe(false);
    expect(d.win.smoother.running).toBe(true);
    // Re-enter bottom strip at far-left icon 2 mid-fold
    const x2 = d.win.restCentre(2);
    d.stepFrame(() => [x2, 678]);
    expect(d.win.active).toBe(true);
    expect(Math.abs(d.win.vX)).toBeLessThan(1);
    expect(Math.abs(d.win.vY)).toBeLessThan(1);
});

test("R13: config reload with folder open and pointer on backdrop drops the dock", () => {
    const d = createDock();
    openFolder6(d);
    // Move into the panel and step just outside its left edge onto the backdrop
    d.runSecs(0.1, () => [700, 350]);
    const bx = d.win.folderPanel.x - 15;
    d.runSecs(0.05, () => [bx, 350]);
    expect(d.win.folderOpen).toBe(true);
    expect(d.win.ptrOnBackdrop).toBe(true);
    expect(d.win.ptrInWin).toBe(false);
    d.reloadItems();
    expect(d.win.active).toBe(false);
    expect(d.win.dropMode).toBe("unit");
});

test("R10 & R14: hide() takes no mode parameter and pressHold is removed", () => {
    expect(dockSrc).toMatch(/^\s*function hide\(\)/m);
    expect(dockSrc).not.toContain("pressHold");
});

test("hovering old panel area during/after unit drop does not re-reveal; bottom edge reveals dock alone", () => {
    const d = createDock();
    openFolder6(d);
    d.win.closeFolder("unit");
    expect(d.win.active).toBe(false);
    expect(d.win.panelDropping).toBe(true);
    // Hover old panel area mid-drop
    d.runSecs(0.1, () => [700, 350]);
    expect(d.win.active).toBe(false);
    // Touch bottom edge: waits revealDelayMs then brings dock back alone, no folder
    d.runSecs(0.05, () => [700, 678]);
    expect(d.win.active).toBe(false);
    d.runSecs((config.behavior.revealDelayMs || 0) / 1000 + 0.02, () => [700, 678]);
    expect(d.win.active).toBe(true);
    expect(d.win.folderOpen).toBe(false);
});

test("switching folders then shrinking eases residual pin shift smoothly back to 0", () => {
    const d = createDock();
    openFolder6(d);
    const f7x = d.win.lefts[7] + d.win.sizes[7] / 2;
    d.win.switchTo(7);
    d.runSecs(0.25, () => [f7x, 620]);
    expect(d.win.folderIndex).toBe(7);
    expect(Math.abs(d.win.lastPs)).toBeGreaterThan(20);
    // Shrink-close onto dock: ps eases smoothly to 0 as panelG shrinks (no 1-frame snap)
    let prevPs = d.win.lastPs;
    d.win.closeFolder("shrink");
    let maxStep = 0;
    d.runSecs(0.30, () => [f7x, 620], (_, w) => {
        maxStep = Math.max(maxStep, Math.abs(w.lastPs - prevPs));
        prevPs = w.lastPs;
    });
    expect(d.win.panelG).toBe(0);
    expect(d.win.lastPs).toBe(0);
    expect(d.win.closeShift).toBe(0);
    expect(maxStep).toBeLessThan(15);
});

test("aborting an unlatched open retracts cleanly back to 0 without sticking", () => {
    const d2 = createDock();
    const f6 = d2.win.restCentre(6);
    // Reveal on neighbour app 5 first so folder 6 hasn't already latched before we step onto it
    revealAt(d2, d2.win.restCentre(5), 620);
    let started = false;
    for (let i = 0; i < 200; i++) {
        d2.stepFrame(() => [f6, 620]);
        if (d2.win.opening && !d2.win.folderOpen && d2.win.panelG > 0.1) { started = true; break; }
    }
    expect(started).toBe(true);
    // Whip sideways at 800 px/s -> must cancel and retract cleanly to panelG === 0
    d2.runSecs(0.30, t => [f6 + 800 * t, 620]);
    expect(d2.win.folderOpen).toBe(false);
    expect(d2.win.opening).toBe(false);
    expect(d2.win.panelG).toBe(0);
    expect(d2.win.centerW).toBe(0);
});

test("clicking open folder icon shrinks it, keeps icon steady, and dismissed blocks reopen until exit", () => {
    const d = createDock();
    openFolder6(d);
    const cxBefore = d.win.lefts[6] + d.win.sizes[6] / 2;
    d.win.closeFolder("shrink");
    d.runSecs(0.6, () => [cxBefore, 620]);
    expect(d.win.folderOpen).toBe(false);
    expect(d.win.panelG).toBe(0);
    expect(Math.abs((d.win.lefts[6] + d.win.sizes[6] / 2) - cxBefore)).toBeLessThan(1);
});

test("dock reveal delay: brief bottom-edge brush stays hidden; holding reveals and magnifies", () => {
    const d = createDock();
    const x = d.win.restCentre(5);
    const delaySec = config.behavior.revealDelayMs / 1000;
    expect(delaySec).toBeGreaterThan(0.08);
    // Brief edge brush (< revealDelayMs) and leave -> never activates
    d.runSecs(delaySec * 0.6, () => [x, 678]);
    expect(d.win.active).toBe(false);
    d.runSecs(0.25, () => [x, 400]);
    expect(d.win.active).toBe(false);
    // Holding at bottom edge past revealDelayMs -> activates and magnifies without needing extra motion
    d.runSecs(delaySec * 0.7, () => [x, 678]);
    expect(d.win.active).toBe(false);
    d.runSecs(delaySec * 0.5, () => [x, 678]);
    expect(d.win.active).toBe(true);
    expect(d.win.magnifying).toBe(true);
    // Mid-fold re-entry catches immediately without waiting revealDelayMs again
    d.runSecs(0.12, () => [x, 620]);
    d.runSecs(0.08, () => [x, 200]); // leaves past regionGraceMs=50 -> folding
    expect(d.win.active).toBe(false);
    expect(d.win.dockHidden).toBe(false);
    d.stepFrame(() => [x, 678]);
    expect(d.win.active).toBe(true);
});

test("responsiveness: resting on folder with 1-2 px hand tremor opens smoothly", () => {
    for (const amp of [1, 2]) {
        const d = createDock();
        const f6 = d.win.restCentre(6);
        revealAt(d, f6, 620);
        let tFirst = null, tFull = null;
        d.runSecs(1.0, t => [f6 + amp * Math.sin(t * 2 * Math.PI * 3), 620], (t, w) => {
            if (tFirst === null && w.panelG > 0.05) tFirst = t;
            if (tFull === null && w.panelG > 0.95) tFull = t;
        });
        expect(d.win.folderOpen).toBe(true);
        expect(tFirst).not.toBeNull();
        expect(tFirst).toBeLessThan(0.32);
        expect(tFull).not.toBeNull();
        expect(tFull).toBeLessThan(0.50);
    }
});

test("responsiveness: slide-and-stop on folder opens promptly without 200ms filter lag", () => {
    const d = createDock();
    const f6 = d.win.restCentre(6), start = f6 - 180;
    revealAt(d, start, 620);
    d.runSecs(0.3, t => [start + 600 * t, 620]);
    let tFirst = null, tFull = null;
    d.runSecs(0.7, () => [f6, 620], (t, w) => {
        if (tFirst === null && w.panelG > 0.05) tFirst = t;
        if (tFull === null && w.panelG > 0.95) tFull = t;
    });
    expect(d.win.folderOpen).toBe(true);
    expect(tFirst).toBeLessThan(0.28);
    expect(tFull).toBeLessThan(0.44);
});

test("responsiveness: 2px horizontal nudge mid-open does not abort an opening folder", () => {
    const d = createDock();
    const f6 = d.win.restCentre(6);
    revealAt(d, d.win.restCentre(5), 620);
    let started = false;
    for (let i = 0; i < 200; i++) {
        d.stepFrame(() => [f6, 620]);
        if (d.win.opening && !d.win.folderOpen && d.win.panelG > 0.15) { started = true; break; }
    }
    expect(started).toBe(true);
    d.runSecs(0.02, t => [f6 + (t / 0.02) * 2, 620]);
    d.runSecs(0.3, () => [f6 + 2, 620]);
    expect(d.win.folderOpen).toBe(true);
});

test("responsiveness: human arc (ease onto folder then rise into panel) opens cleanly", () => {
    for (const pauseMs of [0, 30, 60]) {
        const d = createDock();
        const f6 = d.win.restCentre(6), start = f6 - 160;
        revealAt(d, start, 620);
        d.runSecs(0.20, t => {
            const u = Math.min(1, t / 0.20);
            return [start + 160 * (1 - (1 - u) * (1 - u)), 620];
        });
        if (pauseMs > 0) d.runSecs(pauseMs / 1000, () => [f6, 620]);
        const dur = 220 / 450;
        d.runSecs(dur, t => [f6 + 450 * Math.sin(12 * Math.PI / 180) * t, 620 - 450 * Math.cos(12 * Math.PI / 180) * t]);
        d.runSecs(0.3, () => [f6 + 450 * Math.sin(12 * Math.PI / 180) * dur, 400]);
        expect(d.win.active).toBe(true);
        expect(d.win.folderOpen).toBe(true);
        expect(d.win.folderIndex).toBe(6);
    }
});

test("responsiveness: switching folders and moving to an adjacent app icon feel snappy", () => {
    // Folder 6 -> neighbor folder 7
    const d1 = createDock();
    openFolder6(d1);
    const f6 = d1.win.lefts[6] + d1.win.sizes[6] / 2;
    const f7 = d1.win.lefts[7] + d1.win.sizes[7] / 2;
    d1.runSecs(0.10, t => [f6 + (f7 - f6) * (t / 0.10), 620]);
    let tSwitch = null;
    d1.runSecs(0.4, () => [f7, 620], (t, w) => { if (tSwitch === null && w.folderIndex === 7) tSwitch = t; });
    expect(d1.win.folderIndex).toBe(7);
    expect(tSwitch).not.toBeNull();
    expect(tSwitch).toBeLessThan(0.16);

    // Folder 6 -> adjacent app icon 5 (slow move) closes briskly and keeps dock up
    const d2 = createDock();
    openFolder6(d2);
    const f6b = d2.win.lefts[6] + d2.win.sizes[6] / 2;
    const a5 = d2.win.lefts[5] + d2.win.sizes[5] / 2;
    let tClose = null;
    d2.runSecs(0.25, t => [f6b + (a5 - f6b) * (t / 0.25), 620], (t, w) => { if (tClose === null && !w.folderOpen) tClose = t; });
    d2.runSecs(0.35, () => [a5, 620], (t, w) => { if (tClose === null && !w.folderOpen) tClose = 0.25 + t; });
    expect(d2.win.active).toBe(true);
    expect(d2.win.folderOpen).toBe(false);
    expect(tClose).not.toBeNull();
    expect(tClose).toBeLessThan(0.26);
});
