#!/bin/bash
# Layout probe: boot a throwaway headless GNOME Shell, load the widget against a
# fake snapshot, open the menu, and log real widget allocations + CSS warnings.
#   dev/probe.sh
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Capture the real session state *before* any isolation, so we can prove the
# throwaway shell did not modify it (it must not: this is a live desktop).
REAL_DCONF_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
REAL_ENABLED="$(XDG_CONFIG_HOME="$REAL_DCONF_CONFIG" gsettings get org.gnome.shell enabled-extensions 2>/dev/null)"
TMP=/tmp/cm-probe
rm -rf "$TMP"; mkdir -p "$TMP"/{conf,data,run,cache}
export XDG_CONFIG_HOME="$TMP/conf" XDG_DATA_HOME="$TMP/data" XDG_CACHE_HOME="$TMP/cache" XDG_RUNTIME_DIR="$TMP/run"
# dconf writes escape XDG_CONFIG_HOME (the dconf service resolves the real
# $HOME), so give the throwaway shell its own dconf database via a profile.
# Without this, enabling extensions here rewrites the *live* enabled-extensions.
printf 'user-db:probe\nsystem-db:local\n' > "$TMP/dconf-profile"
export DCONF_PROFILE="$TMP/dconf-profile"
EXT="$XDG_DATA_HOME/gnome-shell/extensions"
mkdir -p "$EXT" "$XDG_RUNTIME_DIR/credit-mon" "$TMP/conf/credit-mon"
cp -r "$REPO/extension" "$EXT/credit-mon@local"
python3 "$REPO/dev/fake_state.py" "$XDG_RUNTIME_DIR/credit-mon/state.json" >/dev/null
cat > "$TMP/conf/credit-mon/config.json" <<'JSON'
{"panel":{"order":["deepseek","moonshot","zai"],"show_provider_names":true,
          "low_balance":{"deepseek":2.0,"moonshot":5.0,"zai":5.0},
          "accents":{"deepseek":"#4d6bfe","moonshot":"#c084fc","zai":"#2dd4bf"}}}
JSON

mkdir -p "$EXT/cm-probe@local"
cat > "$EXT/cm-probe@local/metadata.json" <<'JSON'
{"uuid":"cm-probe@local","name":"cm probe","description":"layout probe","shell-version":["45","46","47","48","49","50"]}
JSON
cat > "$EXT/cm-probe@local/extension.js" <<'JS'
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {CreditMonitorButton} from '../credit-mon@local/extension.js';

export default class Probe extends Extension {
    enable() {
        const dir = this.path ?? GLib.build_filenamev([GLib.get_user_data_dir(), 'gnome-shell',
            'extensions', 'credit-mon@local']);
        try {
            const css = Gio.File.new_for_path(`${dir}/../credit-mon@local/stylesheet.css`);
            St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(css);
            log(`PROBE loaded css ${css.get_path()}`);
        } catch (e) {
            log(`PROBE css load failed: ${e}`);
        }
        this._b = new CreditMonitorButton();
        Main.panel.addToStatusArea('cm-probe', this._b);
        this._t = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2500, () => {
            this._b.menu.open(false);
            GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
                this._dump();
                return GLib.SOURCE_REMOVE;
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    _dump() {
        const St = imports.gi.St;
        const em = Main.extensionManager;
        log(`PROBE ext states: ${['credit-mon@local', 'cm-probe@local'].map(u => `${u}=${em.lookup(u)?.state}`).join(' ')}`);
        const theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
        log(`PROBE stylesheets: ${theme.get_custom_stylesheets().map(f => f.get_path() ?? f.get_uri()).join(' | ')}`);
        const probeBox = new St.BoxLayout({style_class: 'cm-card'});
        probeBox.add_child(new St.Label({text: 'pad test'}));
        global.stage.add_child(probeBox);
        log(`PROBE padding-test (cm-card: pad 14 border 1) ${probeBox.width}x${probeBox.height}`);
        probeBox.destroy();
        const pill = this._b._pill;
        log(`PROBE pill ${pill.width}x${pill.height} children=${pill.get_n_children()}`);
        for (const child of pill.get_children()) {
            log(`PROBE   pill-child ${child.constructor.name} cls="${child.style_class ?? ''}" ${child.width}x${child.height}`);
            for (const sub of child.get_children?.() ?? []) {
                log(`PROBE     ${sub.constructor.name} cls="${sub.style_class ?? ''}" ${sub.width}x${sub.height}`);
            }
        }
        const box = this._b.menu.box;
        log(`PROBE menubox ${box.width}x${box.height} children=${box.get_n_children()}`);
        const walk = (actor, depth) => {
            for (const ch of actor.get_children?.() ?? []) {
                log(`PROBE ${'  '.repeat(depth)}${ch.constructor.name} cls="${ch.style_class ?? ''}" ${ch.width}x${ch.height}`);
                if (depth < 4)
                    walk(ch, depth + 1);
            }
        };
        walk(box, 1);
        log('PROBE done');
    }

    disable() {
        if (this._t)
            GLib.source_remove(this._t);
        this._b?.destroy();
        this._b = null;
    }
}
JS

# Enable over D-Bus after startup (what `gnome-extensions enable` does). Writing
# `enabled-extensions` via gsettings before boot leaves these extensions
# DISABLED in this throwaway profile, so its stylesheet would never load.
gnome-shell --headless --wayland --no-x11 --virtual-monitor 1440x900 > "$TMP/shell.log" 2>&1 &
SPID=$!
sleep 14
ENABLE() {
    gdbus call --session --dest org.gnome.Shell.Extensions \
        --object-path /org/gnome/Shell/Extensions \
        --method org.gnome.Shell.Extensions.EnableExtension "$1" >/dev/null 2>&1
}
# The throwaway dconf profile only keeps one runtime-enabled extension, so the
# probe loads our stylesheet itself with the same St API the shell uses.
ENABLE cm-probe@local
sleep 6
kill $SPID 2>/dev/null; sleep 1

echo "=== extension errors ==="
grep -nE "cm-probe|credit-mon" "$TMP/shell.log" | grep -iE "error|exception|not defined|not a function" | head -10 || true
echo "=== CSS warnings ==="
grep -iE "unknown property|unable to parse|Expected|st_theme_node|could not parse" "$TMP/shell.log" | head -10 || echo "(none)"
echo "=== layout ==="
grep "PROBE" "$TMP/shell.log"

# Guard: the live desktop must be untouched.
AFTER_ENABLED="$(env -u DCONF_PROFILE XDG_CONFIG_HOME="$REAL_DCONF_CONFIG" \
    gsettings get org.gnome.shell enabled-extensions 2>/dev/null)"
if [[ "$AFTER_ENABLED" != "$REAL_ENABLED" ]]; then
    echo "!! HARNESS LEAKED into the live session:"
    echo "   was: $REAL_ENABLED"
    echo "   now: $AFTER_ENABLED"
    env -u DCONF_PROFILE XDG_CONFIG_HOME="$REAL_DCONF_CONFIG" \
        gsettings set org.gnome.shell enabled-extensions "$REAL_ENABLED"
    echo "   restored: $REAL_ENABLED"
    exit 3
fi
echo "live session untouched (enabled-extensions: $AFTER_ENABLED)"
rm -f "$REAL_DCONF_CONFIG/dconf/probe"
