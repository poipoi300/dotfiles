.pragma library

// Pointer-intent maths for the dock. Pure functions, no QML, so they can be tested on their own
// (test/intent.test.js). Coordinates are window pixels, velocities pixels per second, times
// seconds. A rect is {x0, x1, y0, y1}.
//
// Opening: the pointer's straight-line trajectory (position + smoothed velocity relative to the
// folder icon) is tested against the folder icon's visible box. Two numbers come out of it:
//   eta    time until the pointer reaches the icon (0 when already on it)
//   dwell  time it would then stay on the icon (or in the panel funnel above it once engaged)
// and commit = exp(-eta / etaTau) * (1 - exp(-dwell / dwellTau)). A still pointer, or one rising
// into the panel after engaging the icon, has a long dwell (commit near 1); one sliding or slowly
// gliding sideways across the row leaves the icon or triggers the sideways penalty (commit below
// openStart), so passing over a folder never takes over the row. Once openP crosses openStart,
// the dock runs a single unbroken open animation to completion (or cancels it if intent drops).
//
// Closing: outside the open folder's panel and column, a close level builds up at a rate that
// grows with the distance from them and the speed moving away, and drains while the trajectory
// heads back toward them within returnHorizon.

function defaults(openTime, closeTime) {
    const latch = 0.6;
    return {
        latch: latch,                                   // open progress that commits the open
        openStart: 0.5,                                 // open progress that begins one-motion growth
        openCancel: 0.35,                               // integrated openP below this cancels unlatched growth
        // Size openTau so openStart + panel growth lands around openTime rather than delaying
        // the first frame by the full hover delay.
        openTau: (openTime * 0.65) / -Math.log(1 - latch),
        switchTauFactor: 0.55,                          // switching between folders while one is already open
        giveBack: 0.09,                                 // open progress decay when intent drops
        upSpeed: 400,                                   // px/s of upward speed that doubles the open rate
        maxUpBoost: 2.5,                                // cap on upward speed boost
        etaTau: 0.08,
        horizon: 0.4,                                   // ignore icons the pointer reaches later than this
        dwellTau: 0.25,
        sideSpeed: 10,                                  // px/s of sideways excess that divides commit by e
        closeTime: closeTime,                           // still pointer just outside closes in this time
        closeDist: 45,                                  // px of distance that doubles the close rate
        closeSpeed: 300,                                // px/s of receding (approaching) speed that
                                                        // multiplies (divides) it by e
        appCloseMult: 2.5,                              // faster close when resting directly on an app icon
        returnTau: 0.5,
        returnHorizon: 1.5,                             // ignore returns taking longer than this
        closeRelax: 0.1,
        rowHorizon: 1.2,                                // ray hitting the dock bar within this holds drop
        switchCommit: 0.3                               // another folder this likely pauses closing
    };
}

function within(p, r) {
    return p.x >= r.x0 && p.x <= r.x1 && p.y >= r.y0 && p.y <= r.y1;
}

function dist(p, r) {
    const dx = Math.max(r.x0 - p.x, 0, p.x - r.x1);
    const dy = Math.max(r.y0 - p.y, 0, p.y - r.y1);
    return Math.sqrt(dx * dx + dy * dy);
}

function slab(p, v, lo, hi) {
    if (Math.abs(v) < 1e-6) return p >= lo && p <= hi ? [-Infinity, Infinity] : null;
    const a = (lo - p) / v, b = (hi - p) / v;
    return a <= b ? [a, b] : [b, a];
}

// Times the ray p + v t is inside r: {enter, exit} (enter < 0 when it starts inside), or null
// when it never is for t >= 0.
function rayRect(p, v, r) {
    const a = slab(p.x, v.x, r.x0, r.x1);
    if (!a) return null;
    const b = slab(p.y, v.y, r.y0, r.y1);
    if (!b) return null;
    const t0 = Math.max(a[0], b[0]), t1 = Math.min(a[1], b[1]);
    if (t1 < t0 || t1 < 0) return null;
    return { enter: t0, exit: t1 };
}

// Time the ray leaves the union of `rects`, following overlapping rects from time t.
function unionExit(p, v, rects, t) {
    const hits = [];
    for (const r of rects) {
        const h = rayRect(p, v, r);
        if (h) hits.push(h);
    }
    let changed = true;
    while (changed) {
        changed = false;
        for (const h of hits) {
            if (h.enter <= t + 1e-6 && h.exit > t + 1e-6) { t = h.exit; changed = true; }
        }
    }
    return t;
}

// How strongly the pointer is heading into folder icon `icon` to open it (0..1).
// `holds` are the regions that count as staying with the folder once engaged (the panel funnel
// above the icon, and the panel once it shows); `engaged` says being in `holds` counts as on it.
function enterCommit(p, v, icon, holds, engaged, cfg) {
    const all = [icon].concat(holds);
    let on = within(p, icon);
    if (!on && engaged) for (const r of holds) if (within(p, r)) { on = true; break; }
    let eta;
    if (on) {
        eta = 0;
    } else {
        const h = rayRect(p, v, icon);
        if (!h || h.enter > cfg.horizon) return 0;
        eta = Math.max(0, h.enter);
    }
    const dwell = unionExit(p, v, all, eta) - eta;
    const side = Math.max(0, Math.abs(v.x) - Math.max(0, -v.y));
    return Math.exp(-eta / cfg.etaTau - side / cfg.sideSpeed) * (1 - Math.exp(-dwell / cfg.dwellTau));
}

// Exponential approach of x to target with separate time constants up and down.
function approach(x, target, dt, tauUp, tauDown) {
    const tau = target > x ? tauUp : tauDown;
    if (tau <= 0) return target;
    return target + (x - target) * Math.exp(-dt / tau);
}

// Open progress step: follows `commit`, faster while the pointer rises (vy < 0) if `allowBoost`
// is enabled.
function openStep(P, commit, vy, dt, cfg, allowBoost) {
    const boost = allowBoost === false ? 0 : Math.min(cfg.maxUpBoost, Math.max(0, -vy) / cfg.upSpeed);
    const tauUp = cfg.openTau / (1 + boost);
    return approach(P, commit, dt, tauUp, cfg.giveBack);
}

// True when the pointer is moving downward toward the dock bar and will reach it within `horizon`.
function headingToRow(p, v, rowRect, horizon) {
    if (v.y <= 20) return false;
    const h = rayRect(p, v, rowRect);
    return h !== null && h.enter <= horizon;
}

// Where the pointer is relative to the open folder's `home` rects, including the exact
// outward receding speed along the pointer velocity vector (px/s; never spikes when `home`
// itself moves, resizes, or switches).
function homeState(p, v, home) {
    let inside = false, d = Infinity, eta = Infinity, bestDx = 0, bestDy = 0;
    for (const r of home) {
        if (within(p, r)) inside = true;
        const dx = p.x < r.x0 ? p.x - r.x0 : (p.x > r.x1 ? p.x - r.x1 : 0);
        const dy = p.y < r.y0 ? p.y - r.y0 : (p.y > r.y1 ? p.y - r.y1 : 0);
        const dr = Math.sqrt(dx * dx + dy * dy);
        if (dr < d) { d = dr; bestDx = dx; bestDy = dy; }
        const h = rayRect(p, v, r);
        if (h) eta = Math.min(eta, Math.max(0, h.enter));
    }
    if (inside) return { inside: true, dist: 0, eta: 0, recede: 0 };
    const recede = d > 1e-6 ? (v.x * bestDx + v.y * bestDy) / d : 0;
    return { inside: false, dist: d, eta: eta, recede: recede };
}

// Close level step. `recede` is the speed away from home (px/s, negative when approaching).
// Returns the new level; the folder closes at 1. A trajectory that gets back home within
// cfg.returnHorizon drains the level (faster the sooner it arrives); otherwise it builds.
// Optional `mult` accelerates buildup when the pointer is directly on another dock app icon.
function closeStep(I, dt, s, recede, cfg, mult) {
    if (s.inside) return I * Math.exp(-dt / (0.6 * cfg.closeRelax));
    if (s.eta <= cfg.returnHorizon) return I * Math.exp(-dt * Math.exp(-s.eta / cfg.returnTau) / cfg.closeRelax);
    const speed = Math.exp(Math.max(-2.5, Math.min(2.5, recede / cfg.closeSpeed)));
    return I + (mult || 1) * (1 + s.dist / cfg.closeDist) * speed / cfg.closeTime * dt;
}

// Sustained horizontal velocity from a short/long displacement history [{t, x}].
// Preserves exact steady sideways glides (even 5-8 px/s), drops to 0 in `wShort` on braking
// (no long EMA tail), and cancels oscillatory 1-3 px hand tremor where the two windows disagree.
function sustainedVelocity(hist, now, x, wShort, wLong) {
    if (!hist || hist.length < 2) return 0;
    let s0 = hist[0], l0 = hist[0];
    const tS = now - wShort, tL = now - wLong;
    for (let i = 0; i < hist.length; i++) {
        const h = hist[i];
        if (h.t <= tS) s0 = h;
        if (h.t <= tL) l0 = h;
    }
    const dtS = now - s0.t, dtL = now - l0.t;
    if (dtS < 1e-4 || dtL < 1e-4) return 0;
    const vS = (x - s0.x) / Math.max(wShort, dtS);
    const vL = (x - l0.x) / Math.max(wLong, dtL);
    if (vS * vL <= 0) return 0;
    return Math.sign(vS) * Math.min(Math.abs(vS), Math.abs(vL));
}

// Icon-relative horizontal velocity for folder-enter intent. Keeps the wave's opposing slip
// amplification during sideways glides while ignoring pure wave-settling velocity once the
// pointer has stopped.
function waveSlipVx(vx, iv) {
    if (Math.abs(vx) < 1e-4) return 0;
    const slip = (vx * iv < 0)
        ? Math.sign(iv) * Math.min(Math.abs(iv), 2.6 * Math.abs(vx))
        : iv;
    return vx - slip;
}

// Displayed content coordinate of window x: slot k covers [k, k + 1) where the slot is the icon
// plus half a gap on each side as currently laid out; past the ends it continues at the resting
// pitch.
function uFromX(x, lefts, sizes, gap, pitch) {
    const n = lefts.length;
    if (!n) return -1;
    const start = lefts[0] - gap / 2;
    if (x < start) return (x - start) / pitch;
    for (let k = 0; k < n; k++) {
        const w = sizes[k] + gap;
        const s = lefts[k] - gap / 2;
        if (x < s + w) return k + (x - s) / w;
    }
    return n + (x - (lefts[n - 1] + sizes[n - 1] + gap / 2)) / pitch;
}

// Inverse of uFromX: window x of displayed content coordinate u.
function posOfU(u, lefts, sizes, gap, pitch) {
    const n = lefts.length;
    if (!n) return 0;
    if (u <= 0) return lefts[0] - gap / 2 + u * pitch;
    if (u >= n) return lefts[n - 1] + sizes[n - 1] + gap / 2 + (u - n) * pitch;
    const k = Math.min(n - 1, Math.floor(u));
    return lefts[k] - gap / 2 + (u - k) * (sizes[k] + gap);
}

function smooth01(t) {
    t = Math.max(0, Math.min(1, t));
    return t * t * (3 - 2 * t);
}
