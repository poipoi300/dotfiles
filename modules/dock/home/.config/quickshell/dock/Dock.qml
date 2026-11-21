import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Wayland
import Quickshell.Io
import "Intent.js" as Intent

PanelWindow {
    id: win

    // ---- geometry (window coordinates; window is anchored bottom-centre) ----
    // Wide enough for the magnified row, the input zone and the folder panel. Only changes
    // with the config, never per frame.
    readonly property real winW: Math.max(1400, Math.ceil(restW + 2 * zoneExt + 64))
    readonly property real winH: 680
    readonly property real barH: 48
    readonly property real bottomMargin: 8
    readonly property real barBottom: winH - bottomMargin
    readonly property real iconBottom: barBottom - 11
    readonly property real padX: 12

    readonly property var items: Items.items
    readonly property int n: items.length
    readonly property real base: Items.iconCfg.size
    readonly property real peak: Items.iconCfg.peak
    readonly property real gap: Items.iconCfg.spacing
    readonly property real pitch: base + gap
    readonly property real restW: Math.max(0, n * pitch - gap)
    readonly property real restLeft: (winW - restW) / 2
    readonly property real barZone: bottomMargin + barH + 8
    readonly property int waveSlots: Math.max(1, Items.iconCfg.waveSlots)
    // The wave fades out once the pointer is this far past the centre of an end icon
    // (just beyond the magnified icon's outer edge) and is gone fadeLen later.
    readonly property real fadeStart: peak / 2 + 1.5 * gap
    readonly property real fadeLen: 0.75 * peak
    // Largest growth on one side of the pointer (sampled once per config): how far the row can
    // extend past its resting ends.
    readonly property real maxSide: {
        let best = 0;
        for (let c = 0; c <= 1; c += 0.05) {
            let g = 0;
            for (let k = 0; k <= waveSlots + 1; k++) {
                const d = c + k;           // slot centres left of the pointer, nearest first
                g += k === 0 ? c * waveWeight(Math.abs(c - 0.5)) : waveWeight(d - 0.5);
            }
            best = Math.max(best, g);
        }
        return (peak - base) * best;
    }
    // input zone reaches this far past the resting ends (wide enough for a shrink-shifted bar too)
    readonly property real zoneExt: Math.max(maxSide * 1.5 + padX, fadeStart + fadeLen - base / 2) + 16

    // ---- state ----
    property bool active: false          // dock revealed (or rising)
    property bool magnifying: false      // pointer reached the bar band; icons magnified
    property bool sim: false             // test hook (ipc): a synthetic pointer is on the dock
    property real px: winW / 2           // wave position the layout is computed for
    property real pxTarget: winW / 2
    property var sizes: []
    property var lefts: []
    property int hoverIndex: -1
    property real magT: 0                // effective magnification (hoverT x fold x end-of-row fade)
    property real hoverT: 0

    // Pointer, window coordinates. While a folder is open the backdrop window feeds it too, so
    // the trajectory is known outside the dock.
    readonly property bool ptrInWin: ptrHover.hovered || sim
    property bool ptrOnBackdrop: false
    property real ptrX: winW / 2
    property real ptrY: winH
    property real lastX: winW / 2
    property real lastY: winH
    property real vX: 0                  // smoothed pointer velocity, px/s
    property real vY: 0
    property real simClock: 0            // monotonic frame clock for history windows (s)
    property var ptrHist: []             // recent {t, x} samples for sustained sideways velocity
    property real sustVx: 0              // tremor-filtered horizontal velocity, px/s
    property var centers: []             // icon centres in the last frame
    property var iconV: []               // smoothed horizontal velocity of each icon centre

    // Folders open and close by intent (Intent.js): `openP` follows how strongly the pointer
    // heads into the candidate folder and latches the open at cfg.latch; while open, `closeI`
    // builds up when the pointer leaves the panel and its column, and `switchP` tracks another
    // folder the pointer heads into.
    readonly property var cfg: Intent.defaults(Items.behavior.folderHoverDelayMs / 1000,
                                               Items.behavior.folderLeaveDelayMs / 1000)
    // While a folder is open, sliding along the row onto another folder switches briskly: the
    // panel is already up, so that only swaps its content. Only opening is gated on sideways speed.
    readonly property var switchCfg: Object.assign({}, cfg, {
        sideSpeed: Infinity,
        openTau: cfg.openTau * cfg.switchTauFactor
    })
    property int folderIndex: -1         // folder being opened or open
    property bool folderTouched: false   // the pointer has been on that folder's icon since
    property real touchSlowDwell: 0      // time spent slow on the candidate icon (s)
    property real iconIdle: 0            // consecutive motionless time on the candidate icon (s)
    property real peakUpSpeed: 0         // peak upward speed since touching candidate icon (px/s)
    property bool riseEngaged: false     // slow-touch or deceleration unlocked funnel hold & boost
    onFolderIndexChanged: {
        folderTouched = false;
        touchSlowDwell = 0;
        iconIdle = 0;
        peakUpSpeed = 0;
        riseEngaged = false;
        if (folderIndex >= 0) panelAnchorIndex = folderIndex;
        else if (!folderOpen && !opening && panelG < 0.01) folderPanel.clear();
    }
    property bool folderOpen: false      // open latched (by intent or click)
    onFolderOpenChanged: if (!folderOpen) ptrOnBackdrop = false
    property bool opening: false         // one-motion open transition running toward 1
    property int panelAnchorIndex: -1    // icon the panel stays anchored to while shrinking
    property bool clickOpened: false
    property real openP: 0
    property int switchIndex: -1
    property real switchP: 0
    property real closeI: 0
    property real recede: 0              // outward speed away from the open folder, px/s
    property int dismissed: -1           // closed while the pointer is on it: no reopen until it leaves
    property var navHold: null           // panel rect before a level change: still home until left
    property real panelG: 0              // drawn panel growth (0..1)
    property real centerW: 0             // how far the row is anchored on the folder icon (0..1)
    property real anchorX: winW / 2      // smoothed resting centre of that icon
    property int pinIndex: -1            // icon kept in place while anchored
    property real pinX: 0                // where it is kept
    property real closeShift: 0          // residual pin shift eased back to 0 as panel shrinks
    property real lastPs: 0              // horizontal shift applied in the last relayout()

    // Hiding is one animation, never a pause first. "fold": the icons shrink back while the bar
    // slides down, on the same curve. "unit": dock and open panel drop off the screen together
    // (leaving a folder); the panel never comes back up with the dock.
    readonly property real foldDist: barH + bottomMargin + 14
    property real dockY: foldDist
    property real foldStartY: 0
    property bool dockHidden: true
    property string dropMode: "fold"
    property bool panelDropping: false
    property real panelY: 0

    readonly property bool suppressed:
        (Items.behavior.hideOnFullscreen && Windows.activeFullscreen)
        || (Items.behavior.hideOnMaximized && Windows.activeMaximized)
    onActiveChanged: if (active) Items.checkDirs()
    onSuppressedChanged: if (suppressed) { revealTimer.stop(); hide(); }

    readonly property bool wantMag: active && magnifying && ptrInWin
    readonly property real hoverTarget: (folderOpen || opening || panelG > 0.05 || wantMag) ? 1 : 0
    onHoverTargetChanged: smoother.start()
    onWantMagChanged: smoother.start()

    // Pointer events only store the pointer. One FrameAnimation does everything else once per
    // rendered frame (velocity, intent, wave, layout) and stops itself when settled.
    FrameAnimation {
        id: smoother
        onTriggered: if (frameTime > 0) win.tick(frameTime)
    }

    // ---- window ----
    implicitWidth: winW
    implicitHeight: winH
    anchors.bottom: true
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.namespace: "dock"
    WlrLayershell.keyboardFocus: folderOpen
        ? (clickOpened ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand)
        : WlrKeyboardFocus.None

    // Only the dock zone (a thin strip while hidden) and the panel (opening or open) take input.
    mask: Region {
        item: zone
        Region { item: panelMask }
    }

    // ---- layout of the magnification wave ----
    // Content coordinate: slot i covers [i, i+1) (icon plus half a gap each side), so the
    // resting icon centre is i + 0.5. The wave position's resting coordinate u is clamped to the
    // centres of the end icons; that point c is the wave's peak. Only real slots grow.
    //
    // Slot i is laid out at its resting position plus the growth of the slots before it, minus
    // the growth before c. So the point c sits exactly under px (the icon being magnified stays
    // under the pointer), and the row grows outward from it in both directions by the amount
    // each side actually grew: no virtual slots, no empty magnified space. Past an end the
    // layout is held with the end icon (the largest) at its resting centre, and the wave fades
    // out smoothly once px passes the end icon's outer edge (fadeStart .. fadeStart + fadeLen).
    // The resting row never moves; the background just covers whatever the icons span.
    //
    // px normally follows the pointer. While a folder opens (by centerW) it is pulled to the
    // folder icon's resting centre, which centres the wave on that icon, and the row is shifted
    // so the icon stays at pinX, where it was when the opening began: the pointer stays on it,
    // the panel centres over it, and only the neighbours rebalance.
    function waveWeight(d) {
        const R = waveSlots;
        return d >= R ? 0 : Math.pow(0.5 * (1 + Math.cos(Math.PI * d / R)), 1.25);
    }
    // Row laid out for wave position x at magnification mag (before the end-of-row fade).
    function waveAt(x, mag) {
        const cnt = n;
        const u = (x - restLeft + gap / 2) / pitch;
        const c = Math.max(0.5, Math.min(cnt - 0.5, u));
        const out = Math.max(0, u - (cnt - 0.5), 0.5 - u) * pitch;
        const t = Math.max(0, Math.min(1, (out - fadeStart) / fadeLen));
        const m = mag * (1 - t * t * (3 - 2 * t));
        const amp = (peak - base) * m;
        const R = waveSlots;
        const k = Math.min(cnt - 1, Math.floor(c));
        const s = new Array(cnt);
        const l = new Array(cnt);
        let g = 0, gc = 0;
        for (let i = 0; i < cnt; i++) {
            const d = Math.abs(c - (i + 0.5));
            const w = amp > 0 && d < R ? amp * waveWeight(d) : 0;
            s[i] = base + w;
            l[i] = restLeft + i * pitch + g;
            if (i === k) gc = g + (c - k) * w;
            g += w;
        }
        for (let i = 0; i < cnt; i++) l[i] -= gc;
        return { s: s, l: l, m: m };
    }
    function foldMag() {
        if (!active && dropMode === "fold") {
            const span = foldDist - foldStartY;
            return hoverT * (span > 0.1 ? Math.max(0, (foldDist - dockY) / span) : 0);
        }
        return hoverT;
    }
    function relayout() {
        const cnt = n;
        if (!cnt) { sizes = []; lefts = []; magT = 0; lastPs = 0; return; }
        const mag = foldMag();
        const w = waveAt(px, mag);
        // While a folder is opening or open, keep its icon pinned at pinX so neighbours rebalance
        // around it; when shrinking/retracting back to the free dock, ease any residual shift to 0.
        let ps = 0;
        if (pinIndex >= 0 && pinIndex < cnt && (folderOpen || opening)) {
            ps = pinX - (w.l[pinIndex] + w.s[pinIndex] / 2);
        } else if (closeShift !== 0 && panelG > 0) {
            ps = closeShift * panelG;
        }
        lastPs = ps;
        for (let i = 0; i < cnt; i++) w.l[i] += ps;
        sizes = w.s;
        lefts = w.l;
        magT = w.m;
    }
    onNChanged: { centers = []; iconV = []; closeShift = 0; relayout(); }
    // config reloaded: the open folder's slot may now be something else
    onItemsChanged: {
        if (folderOpen || openP > 0 || opening || panelG > 0) closeFolder("auto");
        folderIndex = -1;
        dismissed = -1;
        closeShift = 0;
    }
    onRestLeftChanged: { if (hoverT === 0) { px = winW / 2; pxTarget = px; } relayout(); }
    Component.onCompleted: relayout()

    function restCentre(i) { return restLeft + i * pitch + base / 2; }
    // The visible icon only: not its label, not the slot gaps, not the bar below it (resting on
    // the screen edge after summoning the dock is not intent).
    function iconRect(i) {
        return { x0: lefts[i], x1: lefts[i] + sizes[i], y0: iconBottom - sizes[i] + dockY, y1: iconBottom + dockY };
    }
    // The icon's column. When `toEdge` is true (open folder's home), it spans from the panel
    // down to the bottom screen edge — NOT above the panel, so exiting out the top closes it.
    function columnRect(i, toEdge) {
        return { x0: lefts[i], x1: lefts[i] + sizes[i],
                 y0: toEdge ? folderPanel.y : 0,
                 y1: toEdge ? winH : iconBottom + dockY };
    }
    // Finite panel-area funnel above folder icon `i` (used while opening once rise-engaged, wide
    // enough for diagonal rises up to ~30 deg, finite height so fast vertical exits never latch).
    // Ends just inside the icon's top edge so it bridges `iconRect(i)` upward without covering
    // neighbouring dock icons down on the bar.
    function funnelRect(i) {
        const cx = lefts[i] + sizes[i] / 2;
        const hw = Math.max(sizes[i], folderPanel.width, 340) / 2;
        const top = Math.max(0, folderPanel.bottomY - 260);
        return { x0: cx - hw, x1: cx + hw, y0: top, y1: iconBottom - sizes[i] + dockY + 8 };
    }
    function panelRect() {
        return { x0: folderPanel.x, x1: folderPanel.x + folderPanel.width,
                 y0: folderPanel.y, y1: folderPanel.y + folderPanel.height };
    }
    function rowRect() {
        return { x0: bar.x, x1: bar.x + bar.width, y0: barBottom - barH + dockY, y1: winH };
    }
    function onIcon(x, y) {
        const k = Math.floor(Intent.uFromX(x, lefts, sizes, gap, pitch));
        return k >= 0 && k < n && Intent.within({ x: x, y: y }, iconRect(k));
    }
    // On the displayed bar, or on the icon shown at x (not the padded input zone, which
    // reaches up beside the panel, nor the empty space above shorter icons).
    function onRow(x, y) {
        if (x < bar.x || x > bar.x + bar.width || y > winH) return false;
        if (y >= barBottom - barH + dockY) return true;
        return onIcon(x, y);
    }
    function inZone(x, y) {
        return x >= zone.x && x <= zone.x + zone.width && y >= zone.y && y <= winH;
    }

    // ---- per-frame update ----
    // `elapsed` is the real time since the last frame: velocities divide by it. Progress steps
    // use it capped (dt), so a stalled frame does not jump the animations.
    function tick(elapsed) {
        let busy = false;
        const dt = Math.min(elapsed, 0.05);
        const a = 1 - Math.exp(-elapsed / 0.018);

        // pointer velocity (decays to 0 when no events arrive)
        const dxPtr = ptrX - lastX;
        const dyPtr = ptrY - lastY;
        const moved = dxPtr !== 0 || dyPtr !== 0;
        vX += (dxPtr / elapsed - vX) * a;
        vY += (dyPtr / elapsed - vY) * a;
        simClock += elapsed;
        if (ptrHist.length === 0) ptrHist.push({ t: simClock - elapsed, x: lastX });
        ptrHist.push({ t: simClock, x: ptrX });
        while (ptrHist.length > 2 && ptrHist[0].t < simClock - 0.28) ptrHist.shift();
        sustVx = Intent.sustainedVelocity(ptrHist, simClock, ptrX, 0.05, 0.25);
        lastX = ptrX;
        lastY = ptrY;
        if (!moved && Math.abs(vX) + Math.abs(vY) < 4 && Math.abs(sustVx) < 1) {
            vX = 0; vY = 0; sustVx = 0;
        } else {
            busy = true;
        }

        if (active && updateIntent(elapsed, dt, moved)) busy = true;   // may close the folder or hide the dock
        if (active) {
            // One-motion ease-out growth and centring: launches immediately on frame 1 (no dead
            // ease-in start) and reverses cleanly from any progress.
            const fi = folderIndex;
            const wantOpen = folderOpen || opening;
            if (wantOpen && fi >= 0 && (centerW < 0.01 || pinIndex !== fi)) pin(fi);
            if (fi >= 0) anchorX = Intent.approach(anchorX, restCentre(fi), dt, 0.045, 0.045);
            const boost = riseEngaged ? Math.min(1.0, Math.max(0, -vY) / cfg.upSpeed) : 0;
            const tauGrow = 0.048 / (1 + 0.55 * boost);     // ~140 ms (5..95%) resting, ~95 ms rising
            const tauShrink = 0.038;                        // ~115 ms retract/shrink
            panelG = Intent.approach(panelG, wantOpen ? 1 : 0, dt, tauGrow, tauShrink);
            if (wantOpen && panelG > 0.996) panelG = 1;
            else if (!wantOpen && panelG < 0.004) panelG = 0;
            centerW = (fi >= 0 && wantOpen) ? panelG : 0;
            if (opening && !folderOpen && panelG >= 0.72) latch(false);

            if ((wantOpen && panelG < 1) || (!wantOpen && panelG > 0)
                || (fi >= 0 && Math.abs(anchorX - restCentre(fi)) > 0.2)) {
                busy = true;
            } else if (!wantOpen && panelG === 0) {
                pinIndex = -1;
                closeShift = 0;
                panelAnchorIndex = -1;
                if (folderIndex < 0) folderPanel.clear();
            }

            // Wave position and magnification. Freeze the wave while rising from a candidate
            // folder icon (so diagonal rises keep the icon under the pointer) and hold both
            // wave and magnification while a leave is waiting in regionGrace (no settle step).
            const holdWave = !folderOpen && !opening && fi >= 0 && lefts.length === n && (
                (vY < -60 && Math.abs(vX) <= -vY * 0.7)
                || (folderTouched && ptrY < iconRect(fi).y0
                    && Intent.within({ x: ptrX, y: ptrY }, funnelRect(fi)))
            );
            const leavePending = regionGrace.running
                || (!ptrInWin && !folderOpen && Date.now() < regionAt + regionGraceMs);

            pxTarget = (wantOpen && fi >= 0) ? anchorX : ptrX;
            const kp = 1 - Math.exp(-dt * 38);      // pointer follow, ~26 ms time constant
            const kh = 1 - Math.exp(-dt * 16);      // magnify in/out, ~60 ms
            const dx = pxTarget - px;
            if (!holdWave && !leavePending) {
                if (Math.abs(dx) > 0.15) { px += dx * kp; busy = true; }
                else px = pxTarget;
            }
            const dh = hoverTarget - hoverT;
            if (!leavePending) {
                if (Math.abs(dh) > 0.002) { hoverT += dh * kh; busy = true; } else hoverT = hoverTarget;
            }
        } else if (dockAnim.running) {
            busy = true;                            // fold: magnification shrinks with dockY
        }
        relayout();

        const ai = folderIndex >= 0 ? folderIndex : panelAnchorIndex;
        if (ai >= 0 && ai < n && !panelDropping && lefts.length === n)
            folderPanel.anchorCx = lefts[ai] + sizes[ai] / 2;

        let h = -1;
        if (wantMag && magT > 0.2) {
            const u = Intent.uFromX(ptrX, lefts, sizes, gap, pitch);
            if (u >= 0 && u < n) h = Math.floor(u);
        }
        hoverIndex = h;
        if (!busy) smoother.stop();
    }

    // Returns true while the intent state is still changing.
    function updateIntent(elapsed, dt, moved) {
        const cnt = n;
        if (lefts.length !== cnt || panelDropping) return false;
        const a = 1 - Math.exp(-elapsed / 0.018);
        let cs = centers, iv = iconV;
        if (cs.length !== cnt) {
            cs = []; iv = [];
            for (let i = 0; i < cnt; i++) { cs.push(lefts[i] + sizes[i] / 2); iv.push(0); }
        }
        const ncs = new Array(cnt);
        for (let i = 0; i < cnt; i++) {
            ncs[i] = lefts[i] + sizes[i] / 2;
            iv[i] += ((ncs[i] - cs[i]) / elapsed - iv[i]) * a;
        }
        centers = ncs;
        iconV = iv;

        const tracking = ptrInWin || ptrOnBackdrop;
        const p = { x: ptrX, y: ptrY };
        const pr = panelRect();
        const speedNow = Math.hypot(vX, vY);
        let best = -1, bestC = 0, curC = 0, other = -1, otherC = 0, onFolder = -1, onFolderC = 0;
        for (let i = 0; i < cnt; i++) {
            if (!isFolder(i)) continue;
            const icon = iconRect(i);
            const col = columnRect(i, false);
            if (i === dismissed) {
                if (Intent.within(p, col)) continue;
                dismissed = -1;
            }
            if (!tracking) continue;
            const engaged = i === folderIndex;
            // Funnel hold and upward boost only unlock after a brief slow touch/pause on the icon
            // or deceleration into the panel area, so plain vertical exits never open a folder.
            if (!folderOpen && engaged) {
                if (Intent.within(p, icon)) {
                    folderTouched = true;
                    if (moved) iconIdle = 0;
                    else iconIdle += dt;
                    if (speedNow < 320 && vY <= 30) touchSlowDwell += dt;
                } else {
                    iconIdle = 0;
                }
                if (folderTouched) {
                    if (-vY > peakUpSpeed) peakUpSpeed = -vY;
                    const decelerating = peakUpSpeed > 120 && speedNow < peakUpSpeed * 0.80;
                    if (touchSlowDwell >= 0.025 || iconIdle >= 0.025 || decelerating) riseEngaged = true;
                }
            }
            const holds = folderOpen ? (engaged ? [col] : [])
                : (engaged && riseEngaged ? (panelG > 0.2 ? [funnelRect(i), pr] : [funnelRect(i)]) : []);
            const relVx = folderOpen ? (vX - iv[i]) : Intent.waveSlipVx(sustVx, iv[i]);
            const c = Intent.enterCommit(p, { x: relVx, y: vY }, icon, holds,
                                         !folderOpen && engaged && riseEngaged, folderOpen ? switchCfg : cfg);
            if (engaged) curC = c;
            if (Intent.within(p, icon)) { onFolder = i; onFolderC = c; }
            if (c > bestC) { bestC = c; best = i; }
            if (!engaged && c > otherC) { otherC = c; other = i; }
        }
        // Prefer the folder icon the pointer is actually resting on over a distant folder ahead
        // on the ray so decelerating into a folder engages it immediately.
        if (!folderOpen && onFolder >= 0 && onFolderC > 0) { best = onFolder; bestC = onFolderC; }

        if (!folderOpen) {
            const risingHold = folderIndex >= 0 && folderTouched && !opening && vY < -20
                && Intent.within(p, funnelRect(folderIndex));
            if (best !== folderIndex && (best >= 0 ? (folderIndex < 0 || (!opening && openP < 0.08))
                                                   : (!opening && openP < 0.08 && !risingHold))) {
                folderIndex = best;
                curC = bestC;
            }
            if (folderIndex < 0) { openP = 0; opening = false; return false; }
            openP = Intent.openStep(openP, curC, vY, dt, cfg, riseEngaged);
            // Preload directory/group contents before growth begins so the panel opens at its
            // final size without popping mid-animation.
            if (openP >= 0.15 && !opening && !panelDropping) loadPanel();
            if (openP >= cfg.openStart && !opening) {
                opening = true;
                loadPanel();
            }
            if (opening && openP < cfg.openCancel) {
                closeShift = panelG > 0.01 ? lastPs / panelG : 0;
                opening = false;
                pinIndex = -1;
            }
            if (openP < 0.002 && curC === 0 && !opening && !risingHold) {
                openP = 0;
                if (panelG < 0.01) folderIndex = -1;
                return false;
            }
            return true;
        }

        // open: another folder the pointer heads into takes over
        if (other !== switchIndex && (switchIndex < 0 || switchP < 0.08)) switchIndex = other;
        if (switchIndex >= 0) {
            switchP = Intent.openStep(switchP, switchIndex === other ? otherC : 0, vY, dt, switchCfg, true);
            if (switchP >= cfg.latch) { switchTo(switchIndex); return true; }
            if (switchP < 0.002 && other !== switchIndex) { switchP = 0; switchIndex = -1; }
        }

        // close when the pointer leaves the panel and the folder's column with intent
        const home = [columnRect(folderIndex, true), pr];
        if (navHold) {
            if (Intent.within(p, navHold)) home.push(navHold);
            else navHold = null;
        }
        const s = Intent.homeState(p, { x: vX, y: vY }, home);
        recede = s.recede;
        const slotAt = ptrInWin ? Math.floor(Intent.uFromX(ptrX, lefts, sizes, gap, pitch)) : -1;
        const onSlotIcon = slotAt >= 0 && slotAt < cnt && Intent.within(p, iconRect(slotAt));
        const onAppIcon = onSlotIcon && !isFolder(slotAt);
        const onOtherFolder = onSlotIcon && isFolder(slotAt) && slotAt !== folderIndex;
        const toRow = !(ptrInWin && onRow(ptrX, ptrY))
            && Intent.headingToRow(p, { x: vX, y: vY }, rowRect(), cfg.rowHorizon);
        if (otherC > cfg.switchCommit || onOtherFolder) closeI = Intent.approach(closeI, 0, dt, 0, cfg.closeRelax);
        else closeI = Intent.closeStep(closeI, dt, s, recede, cfg, onAppIcon ? cfg.appCloseMult : 1);
        // Heading back down to the dock bar holds off a premature drop until arrival on the row.
        if (toRow && closeI >= 0.99) closeI = 0.99;
        if (closeI >= 1) { closeFolder("auto"); return true; }
        if (s.inside && closeI < 0.001) closeI = 0;
        return closeI > 0 || !s.inside || switchIndex >= 0;
    }

    // ---- pointer ----
    // The whole window; the mask limits it to the zone and the panel. On top of everything so
    // nothing under it can take the hover away; a hover handler takes no clicks.
    Item {
        anchors.fill: parent
        z: 1000
        HoverHandler {
            id: ptrHover
            onPointChanged: if (hovered) win.pointerAt(point.position.x, point.position.y, true)
            onHoveredChanged: if (!hovered) Qt.callLater(win.pointerLeft)
        }
    }
    function pointerAt(x, y, inWin) {
        if (!smoother.running) {
            // the loop was idle: nothing moved since, so no velocity carries over
            lastX = x;
            lastY = y;
            ptrHist = [{ t: simClock, x: x }];
            sustVx = 0;
            centers = [];
            iconV = [];
        }
        ptrX = x;
        ptrY = y;
        if (inWin) {
            regionGrace.stop();
            lostGrace.stop();
            // only the bottom strip reveals: a stale event through the old panel region does not
            if (!active && inZone(x, y) && !suppressed) {
                const delay = Math.max(0, Items.behavior.revealDelayMs || 0);
                if (delay <= 0 || (!dockHidden && dropMode === "fold")) show();
                else if (!revealTimer.running) revealTimer.restart();
            }
            if (active && !magnifying && y > winH - barZone) magnifying = true;
        }
        if (hoverT < 0.02 && centerW < 0.01) px = x;      // first contact: no slide-in
        smoother.start();
    }
    function revealTriggered() {
        if (active || suppressed || !ptrInWin || !inZone(ptrX, ptrY)) return;
        show();
        if (ptrY > winH - barZone) magnifying = true;
        if (hoverT < 0.02 && centerW < 0.01) px = ptrX;
        smoother.start();
    }
    // Leaving the dock hides it at once, unless a folder is open: then the backdrop keeps
    // tracking the pointer and the close intent decides. The compositor applies a grown input
    // region a frame late, so a fast pointer can "leave" the old region right after the dock
    // (or the panel region) grew; such a leave is checked again once the region is in place.
    property real regionAt: 0              // when the input region last grew
    function regionGrew() { regionAt = Date.now(); }
    readonly property int regionGraceMs: 50
    function pointerLeft() {
        if (ptrHover.hovered || sim) return;
        revealTimer.stop();
        if (!active) return;
        // with a folder open the backdrop takes over; if it does not (pointer on another
        // output, say), the position is stale and the folder drops away
        if (folderOpen) { if (!ptrOnBackdrop) lostGrace.restart(); return; }
        const left = regionAt + regionGraceMs - Date.now();
        if (left > 0) { regionGrace.interval = left; regionGrace.restart(); return; }
        hide();
    }
    Timer {
        id: revealTimer
        interval: Math.max(0, Items.behavior.revealDelayMs || 0)
        onTriggered: win.revealTriggered()
    }
    Timer {
        id: regionGrace
        onTriggered: win.pointerLeft()            // re-checks a region growth since
    }
    Timer {
        id: lostGrace
        interval: 150
        onTriggered: if (win.folderOpen && !ptrHover.hovered && !win.sim && !win.ptrOnBackdrop)
            win.closeFolder("unit")
    }

    // ---- show / hide ----
    NumberAnimation {
        id: dockAnim
        target: win
        property: "dockY"
        onFinished: if (!win.active) win.dockGone()
    }
    NumberAnimation {
        id: panelAnim
        target: win
        property: "panelY"
        onFinished: win.panelGone()
    }
    function show() {
        revealTimer.stop();
        if (active || suppressed) return;
        // re-entering mid-fold: keep the icons the size the fold left them at
        if (dropMode === "fold") hoverT = foldMag();
        active = true;
        dockHidden = false;
        lastX = ptrX;                  // no velocity spike from pre-hide coordinates
        lastY = ptrY;
        ptrHist = [{ t: simClock, x: ptrX }];
        centers = [];                  // no velocities carried over from before
        iconV = [];
        vX = 0;
        vY = 0;
        sustVx = 0;
        dockAnim.stop();
        dockAnim.from = dockY;
        dockAnim.to = 0;
        dockAnim.duration = 210;
        dockAnim.easing.type = Easing.OutCubic;
        dockAnim.start();
        smoother.start();
    }
    function hide() {
        revealTimer.stop();
        if (!active) return;
        const mode = panelG > 0.02 ? "unit" : "fold";
        regionGrace.stop();
        lostGrace.stop();
        if (mode === "fold") { panelG = 0; panelAnchorIndex = -1; closeShift = 0; }
        active = false;
        magnifying = false;
        folderOpen = false;
        opening = false;
        clickOpened = false;
        folderIndex = -1;
        openP = 0;
        closeI = 0;
        switchP = 0;
        switchIndex = -1;
        navHold = null;
        dropMode = mode;
        foldStartY = dockY;
        // Clear the panel and dock off-screen as one rigid unit with immediate frame-1 launch.
        const clearance = Math.max(foldDist, winH - Math.min(barBottom - barH, folderPanel.y) + 28);
        const d = mode === "unit" ? clearance : foldDist;
        const ms = mode === "unit" ? 150 : 175;
        if (mode === "unit") {
            panelDropping = true;
            panelAnim.stop();
            panelAnim.from = panelY;
            panelAnim.to = d;
            panelAnim.duration = ms;
            panelAnim.easing.type = Easing.OutQuad;
            panelAnim.start();
        }
        dockAnim.stop();
        dockAnim.from = dockY;
        dockAnim.to = mode === "unit" ? Math.max(dockY, d) : foldDist;
        dockAnim.duration = ms;
        dockAnim.easing.type = mode === "unit" ? Easing.OutQuad : Easing.OutCubic;
        dockAnim.start();
        smoother.start();
    }
    function dockGone() {
        dockHidden = true;
        dockY = foldDist;              // a later reveal rises the usual distance
        foldStartY = 0;
        hoverT = 0;
        centerW = 0;
        pinIndex = -1;
        panelAnchorIndex = -1;
        closeShift = 0;
        lastPs = 0;
        opening = false;
        panelG = 0;
        px = winW / 2;
        pxTarget = px;
        hoverIndex = -1;
        relayout();
    }
    function panelGone() {
        panelDropping = false;
        panelG = 0;
        panelAnchorIndex = -1;
        panelY = 0;
    }

    // ---- input zone ----
    Item {
        id: zone
        readonly property real hiddenW: win.restW + 80
        readonly property real shownW: win.restW + 2 * win.zoneExt
        readonly property real tall: win.barZone + win.peak + 40
        width: win.suppressed ? 0 : (win.active ? shownW : hiddenW)
        height: win.suppressed ? 0 : win.active ? tall : 4
        x: (win.winW - width) / 2
        y: win.winH - height
        // grows only on reveal (width and height together)
        onHeightChanged: if (win.active) win.regionGrew()
    }
    // Input region of the folder panel: active once opening commits or a rise-engaged pointer
    // heads into the panel (zero-size otherwise so sideways sweeps and fast vertical exits
    // never arm regionGrace).
    Item {
        id: panelMask
        readonly property bool on: !win.panelDropping
            && (win.folderOpen || win.opening || win.panelG > 0.05
                || (win.folderIndex >= 0 && win.riseEngaged && win.openP > 0.20 && win.vY < -30))
        onOnChanged: if (on) win.regionGrew()
        // the panel resizes (160 ms) on navigation: a change while on may be growth
        onWidthChanged: if (on) win.regionGrew()
        onHeightChanged: if (on) win.regionGrew()
        x: folderPanel.x
        y: folderPanel.y
        width: on ? folderPanel.width : 0
        height: on ? folderPanel.height : 0
    }

    function isFolder(i) {
        const t = i >= 0 && i < n ? items[i].type : "";
        return t === "group" || t === "dir";
    }
    function loadPanel() {
        const node = items[folderIndex];
        if (folderPanel.root !== node) folderPanel.openNode(node);
    }
    function latch(byClick) {
        folderOpen = true;
        opening = true;
        clickOpened = byClick;
        openP = 1;
        closeI = 0;
        switchP = 0;
        switchIndex = -1;
        dismissed = -1;
        recede = 0;
        navHold = null;
        loadPanel();
        if (folderIndex >= 0 && lefts.length === n)
            folderPanel.anchorCx = lefts[folderIndex] + sizes[folderIndex] / 2;
        smoother.start();
    }
    // Keep icon i where it is now while the row anchors on it.
    function pin(i) {
        if (lefts.length !== n) return;
        const shown = lefts[i] + sizes[i] / 2;
        if (centerW < 0.01) anchorX = restCentre(i);
        closeShift = 0;
        pinIndex = i;
        pinX = shown;
    }
    function switchTo(i) {
        pin(i);                        // the new folder's icon stays under the pointer
        folderIndex = i;
        switchP = 0;
        switchIndex = -1;
        closeI = 0;
        recede = 0;
        navHold = null;
        loadPanel();
        if (lefts.length === n) folderPanel.anchorCx = lefts[i] + sizes[i] / 2;
        folderPanel.fadeIn();
    }
    // click (or ipc) opens at once
    function openFolder(i, byClick) {
        if (!isFolder(i) || (!active && suppressed)) return;
        if (panelDropping) { panelAnim.stop(); panelGone(); }   // the old panel is gone first
        if (!active) {
            if (hoverT < 0.02 && centerW < 0.01) px = restCentre(i);
            show();
        }
        if (folderOpen) {
            if (i !== folderIndex) switchTo(i);
            clickOpened = clickOpened || byClick;
            return;
        }
        folderIndex = i;
        if (centerW < 0.01 || pinIndex !== i) pin(i);
        latch(byClick);
    }
    // "shrink": the panel folds back into its icon and the dock stays (pointer on the dock).
    // "unit": dock and panel drop away together. "auto" picks by where the pointer is.
    function closeFolder(how) {
        if (!folderOpen && openP <= 0 && !opening && panelG <= 0) return;
        if (how === "auto") how = ptrInWin && onRow(ptrX, ptrY) ? "shrink" : "unit";
        if (how === "unit") { hide(); return; }
        // Ease any residual pin shift back to 0 as the panel shrinks, and let the wave follow
        // the pointer immediately without sliding the dock laterally.
        closeShift = panelG > 0.01 ? lastPs / panelG : 0;
        dismissed = folderIndex;
        folderOpen = false;
        opening = false;
        clickOpened = false;
        folderIndex = -1;
        centerW = 0;
        pinIndex = -1;
        openP = 0;
        closeI = 0;
        switchP = 0;
        switchIndex = -1;
        navHold = null;
        smoother.start();
    }

    // focus the running window, otherwise launch (and bounce the slot)
    function runNode(node, slot) {
        const keys = Items.info(node).keys;
        if (!Windows.focus(keys)) {
            Items.launch(node);
            if (slot >= 0) slotRepeater.itemAt(slot).bounce();
        }
    }

    // ---- visuals ----
    Item {
        id: scene
        anchors.fill: parent
        Item {
            id: dockContent
            anchors.fill: parent
            visible: !win.dockHidden
            transform: Translate { y: win.dockY }

            // Background: covers whatever the icons span (asymmetric while magnifying near an end).
            RectangularShadow {
                anchors.fill: bar
                offset.y: 3
                blur: 18
                spread: -2
                radius: bar.radius
                color: Theme.shadow
            }
            Rectangle {
                id: bar
                readonly property real x0: win.n && win.lefts.length ? win.lefts[0] : win.restLeft
                readonly property real x1: win.n && win.lefts.length
                    ? win.lefts[win.n - 1] + win.sizes[win.n - 1] : win.restLeft + win.restW
                x: x0 - win.padX
                width: x1 - x0 + 2 * win.padX
                y: win.barBottom - win.barH
                height: win.barH
                radius: Math.min(Theme.radius + 4, height / 2)
                color: Theme.bar
                border.width: 1
                border.color: Theme.border
            }

            Repeater {
                id: slotRepeater
                model: win.items
                delegate: DockItem {
                    slotX: win.lefts[slot] !== undefined ? win.lefts[slot] : 0
                    iconSize: win.sizes[slot] !== undefined ? win.sizes[slot] : win.base
                    iconBottom: win.iconBottom
                    shown: dockContent.visible
                    folderShown: win.folderIndex === slot && (win.folderOpen || win.panelG > 0.3)
                    onTapped: {
                        if (win.isFolder(slot)) {
                            if (win.folderOpen && win.folderIndex === slot) win.closeFolder("shrink");
                            else win.openFolder(slot, true);
                        } else {
                            win.closeFolder("shrink");
                            win.runNode(node, slot);
                        }
                    }
                }
            }

            // label above the hovered icon
            RectangularShadow {
                anchors.fill: label
                visible: label.visible
                opacity: label.opacity
                offset.y: 2
                blur: 10
                radius: label.radius
                color: Theme.shadow
            }
            Rectangle {
                id: label
                readonly property int idx: win.hoverIndex
                property int shownIdx: -1          // keeps the text while fading out
                onIdxChanged: if (idx >= 0) shownIdx = idx
                readonly property bool show: idx >= 0 && win.magT > 0.5 && !win.folderOpen
                                             && win.panelG < 0.05 && win.wantMag
                opacity: show ? 1 : 0
                visible: opacity > 0.01
                Behavior on opacity { NumberAnimation { duration: 110 } }
                width: labelText.implicitWidth + 22
                height: labelText.implicitHeight + 10
                radius: Math.min(Theme.radius, height / 2)
                color: Theme.label
                border.width: 1
                border.color: Theme.border
                // follows the icon every frame (no Behavior: that would restart an animation per frame)
                readonly property int at: shownIdx >= 0 && shownIdx < win.n ? shownIdx : -1
                readonly property real cx: at >= 0 ? win.lefts[at] + win.sizes[at] / 2 : win.winW / 2
                x: Math.round(Math.max(4, Math.min(win.winW - width - 4, cx - width / 2)))
                y: Math.round(at >= 0 ? win.iconBottom - win.sizes[at] - height - 10 : win.iconBottom)
                Text {
                    id: labelText
                    anchors.centerIn: parent
                    text: label.at >= 0 ? Items.info(win.items[label.at]).label : ""
                    color: Theme.text
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    font.family: Theme.font
                }
            }
        }

        FolderPanel {
            id: folderPanel
            open: win.folderOpen
            g: win.panelG
            dropY: win.panelY
            bottomY: win.iconBottom - win.peak - 16
            onCloseRequested: win.closeFolder("auto")
            // a hold the pointer is still in outlives further level changes
            onNavigating: if (!win.navHold || !Intent.within({ x: win.ptrX, y: win.ptrY }, win.navHold))
                win.navHold = win.panelRect()
            onRunNode: node => { win.closeFolder("unit"); win.runNode(node, -1); }
        }
    }

    // Persistent full-screen layer under the dock (unmapped when suppressed, zero input mask
    // unless a folder is open). Keeping it warm avoids a per-open EGL surface creation hitch
    // mid-animation while passing all clicks and hovers through when no folder is open.
    PanelWindow {
        id: backdrop
        visible: !win.suppressed
        screen: win.screen
        anchors { top: true; bottom: true; left: true; right: true }
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.namespace: "dock-backdrop"
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        color: "transparent"
        mask: Region { item: backdropHit }
        onVisibleChanged: if (!visible) win.ptrOnBackdrop = false
        Item {
            id: backdropHit
            width: win.folderOpen ? parent.width : 0
            height: win.folderOpen ? parent.height : 0
            HoverHandler {
                enabled: win.folderOpen
                onHoveredChanged: {
                    win.ptrOnBackdrop = hovered && win.folderOpen;
                    if (!hovered) Qt.callLater(win.pointerLeft);
                }
                onPointChanged: if (hovered && win.folderOpen)
                    win.pointerAt(point.position.x - (backdrop.width - win.winW) / 2,
                                  point.position.y - (backdrop.height - win.winH), false)
            }
            TapHandler {
                enabled: win.folderOpen
                acceptedButtons: Qt.AllButtons
                onTapped: win.closeFolder("unit")
            }
        }
    }

    Timer {
        id: benchTimer
        property real t0: 0
        property real dur: 0
        repeat: true
        onTriggered: {
            const t = (Date.now() - t0) / dur;
            if (t >= 1) { running = false; win.sim = false; win.pointerLeft(); return; }
            // sweep left to right and back, twice
            const ph = (t * 4) % 2;
            const f = ph < 1 ? ph : 2 - ph;
            win.pointerAt(win.restLeft + f * win.restW, win.iconBottom - 15, true);
        }
    }

    // test / scripting hooks:  qs -c dock ipc call dock <fn>
    IpcHandler {
        target: "dock"
        function simulate(x: real): void {
            win.sim = true;
            if (!win.active) { win.ptrX = x; win.ptrY = win.iconBottom - 15; win.show(); }
            win.pointerAt(x, win.iconBottom - 15, true);
        }
        function point(x: real, y: real): void {   // synthetic pointer anywhere (window coordinates)
            win.sim = true;
            win.pointerAt(x, y, true);
        }
        function bench(hz: int, ms: int): void {   // synthetic pointer sweep for profiling
            benchTimer.interval = Math.max(1, Math.round(1000 / hz));
            benchTimer.t0 = Date.now(); benchTimer.dur = ms;
            win.sim = true;
            if (!win.active) { win.ptrX = win.restLeft; win.ptrY = win.iconBottom - 15; win.show(); }
            win.magnifying = true;
            benchTimer.running = true;
        }
        function layout(): string {   // geometry dump (window coordinates) for checking the wave
            const r = v => Math.round(v * 10) / 10;
            return JSON.stringify({ px: r(win.px), winW: win.winW, restLeft: win.restLeft, restW: win.restW,
                lefts: win.lefts.map(r), sizes: win.sizes.map(r), magT: r(win.magT), hover: win.hoverIndex,
                barX: r(bar.x), barW: r(bar.width), zoneX: zone.x, zoneW: zone.width });
        }
        function state(): string {    // intent and hide state (window coordinates)
            const r = v => Math.round(v * 1000) / 1000;
            const f = win.folderIndex;
            return JSON.stringify({ active: win.active, hidden: win.dockHidden, dockY: r(win.dockY),
                drop: win.dropMode, folder: f, open: win.folderOpen, click: win.clickOpened,
                openP: r(win.openP), panelG: r(win.panelG), centerW: r(win.centerW), closeI: r(win.closeI),
                switch: win.switchIndex, switchP: r(win.switchP), panelY: r(win.panelY),
                panelDropping: win.panelDropping, ptr: [r(win.ptrX), r(win.ptrY)], v: [r(win.vX), r(win.vY)],
                inWin: win.ptrInWin, onBackdrop: win.ptrOnBackdrop, hover: win.hoverIndex,
                folderIconCx: f >= 0 && win.lefts.length === win.n ? r(win.lefts[f] + win.sizes[f] / 2) : -1,
                panelCx: r(folderPanel.x + folderPanel.width / 2), depth: folderPanel.stack.length,
                panel: [r(folderPanel.x), r(folderPanel.y), r(folderPanel.width), r(folderPanel.height)] });
        }
        function theme(): string {    // resolved DMS palette and roles
            return JSON.stringify({ light: Theme.light, pal: Theme.pal, font: Theme.font, radius: Theme.radius,
                text: String(Theme.text), bar: String(Theme.bar), accent: String(Theme.accent), panel: String(Theme.panel) });
        }
        function shot(path: string): void {   // save the dock window's rendering as a PNG (testing)
            scene.grabToImage(r => r.saveToFile(path));
        }
        function release(): void { benchTimer.stop(); win.sim = false; win.pointerLeft(); }
        function open(i: int): void { win.openFolder(i, true); }
        function close(): void { win.closeFolder("auto"); }
        function enter(name: string): void {   // open sub-folder `name` of the open directory folder
            const c = folderPanel.cur;
            if (c && c.type === "dir") folderPanel.push({ type: "dir", name: name, path: Items.expand(c.path) + "/" + name, sub: true });
        }
        function back(): void { folderPanel.back(); }
        function reveal(): void { win.show(); }
        function hide(): void { benchTimer.stop(); win.sim = false; win.hide(); }
    }
}
