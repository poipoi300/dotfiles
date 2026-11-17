//@ pragma UseQApplication
import Quickshell

ShellRoot {
    // One Dock per chosen screen, recreated when the screen comes back: a
    // monitor that powers off disconnects its output, and a layer surface
    // bound to the vanished output never maps again on its own.
    Variants {
        model: {
            const want = Items.behavior.output;
            const named = Quickshell.screens.filter(s => want && s.name === want);
            if (named.length) return named;
            return Quickshell.screens.length ? [Quickshell.screens[0]] : [];
        }
        Dock {
            required property var modelData
            screen: modelData
        }
    }
}
