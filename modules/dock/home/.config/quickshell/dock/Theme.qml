pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

// The DankMaterialShell palette, font and corner radius, so the dock matches the bar.
// Sources (all watched, nothing polls):
//   $XDG_CONFIG_HOME/DankMaterialShell/settings.json   theme name, custom theme file, font,
//                                                      corner radius, transparency
//   $XDG_STATE_HOME/DankMaterialShell/session.json     light / dark mode
//   $XDG_CACHE_HOME/DankMaterialShell/dms-colors.json  palette DMS generates for every theme
//                                                      (stock, wallpaper/matugen, custom)
//   the custom theme file itself                       exact colours of a custom/registry theme,
//                                                      resolved like DMS (flavour + accent)
// Keys set in config.json's "style" override the theme. Without DMS a built-in dark palette is used.
Singleton {
    id: root

    readonly property string home: Quickshell.env("HOME")
    function xdg(name, fallback) {
        const v = Quickshell.env(name);
        return (v ? v : home + fallback) + "/DankMaterialShell";
    }

    property var settings: ({})
    property bool light: false
    property var generated: null        // dms-colors.json "colors" ({dark, light})
    property var customRaw: null        // parsed custom theme file
    // A file that is missing or being rewritten keeps the last values it gave (no flicker to
    // the fallback palette); the watch picks it up again once it is back.

    function parseJson(text, what) {
        try {
            return JSON.parse(text);
        } catch (e) {
            console.warn("dock: cannot parse " + what + ":", e);
            return null;
        }
    }

    FileView {
        path: root.xdg("XDG_CONFIG_HOME", "/.config") + "/settings.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { const j = root.parseJson(text(), "DMS settings.json"); if (j) root.settings = j; }
    }
    FileView {
        path: root.xdg("XDG_STATE_HOME", "/.local/state") + "/session.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: {
            const j = root.parseJson(text(), "DMS session.json");
            if (j) root.light = !!j.isLightMode;
        }
    }
    FileView {
        path: root.xdg("XDG_CACHE_HOME", "/.cache") + "/dms-colors.json"
        watchChanges: true
        onFileChanged: reload()
        onLoaded: { const j = root.parseJson(text(), "dms-colors.json"); if (j) root.generated = j.colors || null; }
    }
    FileView {
        path: root.settings.currentThemeName === "custom" && root.settings.customThemeFile
            ? root.settings.customThemeFile : ""
        watchChanges: true
        onPathChanged: if (!path) root.customRaw = null
        onFileChanged: reload()
        onLoaded: root.customRaw = root.parseJson(text(), "DMS theme file")
    }

    // ---- palette resolution (DMS role names) ----
    readonly property var fallback: ({
        primary: "#cba6f7", primaryText: "#1e1e2e", primaryContainer: "#55307f",
        surfaceText: "#cdd6f4", surfaceVariantText: "#a6adc8", outline: "#6c7086",
        surfaceContainer: "#1e1e2e", surfaceContainerHigh: "#313244", surfaceContainerHighest: "#45475a",
        shadow: "#000000", error: "#f38ba8"
    })

    function assign(out, src) {
        if (src) for (const k in src) if (src[k]) out[k] = src[k];
        return out;
    }
    function findById(list, id) {
        if (!list) return null;
        for (const v of list) if (v.id === id) return v;
        return null;
    }
    // Same resolution as DMS Theme.loadCustomTheme(): base mode colours, then the selected flavour
    // and accent (multi-variant registry themes) or the selected option.
    function resolveCustom(t, mode) {
        if (!t) return null;
        const out = assign({}, (t.dark || t.light) ? (t[mode] || t.dark || t.light) : t);
        const vars = t.variants;
        if (!vars) return out;
        const all = root.settings.registryThemeVariants || {};
        const stored = all[t.id || ""];
        if (vars.type === "multi" && vars.flavors && vars.accents) {
            const defs = vars.defaults || {};
            const md = defs[mode] || defs.dark || {};
            let sel = md;
            if (stored && typeof stored === "object")
                sel = (stored.dark || stored.light) ? (stored[mode] || md) : stored;
            let flavor = findById(vars.flavors, sel.flavor || md.flavor);
            if (flavor && !(flavor[mode] && (flavor[mode].primary || flavor[mode].surface)))
                flavor = findById(vars.flavors, md.flavor);
            const accent = findById(vars.accents, sel.accent || md.accent) || vars.accents[0];
            if (flavor) {
                assign(out, flavor[mode] || flavor.dark || flavor.light);
                if (accent) assign(out, accent[flavor.id]);
            }
        } else if (vars.options && vars.options.length) {
            const v = findById(vars.options, typeof stored === "string" ? stored : vars.default);
            if (v) assign(out, v[mode] || v.dark || v.light);
        }
        return out;
    }

    readonly property var pal: {
        const mode = light ? "light" : "dark";
        const out = assign({}, fallback);
        const g = generated ? generated[mode] : null;
        if (g) assign(out, {
            primary: g.primary, primaryText: g.on_primary, primaryContainer: g.primary_container,
            surfaceText: g.on_background || g.on_surface, surfaceVariantText: g.on_surface_variant,
            outline: g.outline, surfaceContainer: g.surface_container,
            surfaceContainerHigh: g.surface_container_high, surfaceContainerHighest: g.surface_container_highest,
            shadow: g.shadow, error: g.error
        });
        if (customRaw) assign(out, resolveCustom(customRaw, mode));
        return out;
    }

    // ---- roles used by the dock (config.json "style" keys override) ----
    readonly property var ov: Items.style
    function a(c, alpha) { return Qt.rgba(c.r, c.g, c.b, alpha); }
    function num(v, d) { return typeof v === "number" ? v : d; }

    readonly property color primary: ov.accent || pal.primary
    readonly property color primaryFg: pal.primaryText
    readonly property color surface: pal.surfaceContainer
    readonly property color surfaceHigh: pal.surfaceContainerHigh
    readonly property color surfaceHighest: pal.surfaceContainerHighest
    readonly property color surfaceFg: ov.text || pal.surfaceText
    readonly property color surfaceFgVariant: pal.surfaceVariantText
    readonly property color shadowBase: pal.shadow || "#000000"

    readonly property real dockAlpha: num(settings.dockTransparency, 0.85)
    readonly property real popupAlpha: num(settings.popupTransparency, 0.92)

    readonly property string font: ov.font || settings.fontFamily || "sans-serif"
    readonly property real radius: num(ov.radius, num(settings.cornerRadius, 12))

    readonly property color text: surfaceFg
    readonly property color textDim: a(surfaceFg, 0.62)
    readonly property color accent: primary
    readonly property color bar: ov.background || a(surface, dockAlpha)
    readonly property color border: ov.border || a(surfaceFg, light ? 0.10 : 0.07)
    readonly property color panel: ov.panelBackground || a(surface, Math.max(popupAlpha, 0.9))
    readonly property color label: ov.labelBackground || a(surfaceHigh, 0.97)
    readonly property color hover: a(primary, 0.16)
    readonly property color pressed: a(primary, 0.26)
    readonly property color tile: a(surfaceHighest, 0.85)         // folder previews, letter tiles
    readonly property color shadow: a(shadowBase, light ? 0.20 : 0.42)
}
