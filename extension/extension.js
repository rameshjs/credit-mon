/* credit-mon panel extension
 *
 * The extension is a pure view: it reads a JSON snapshot written by the
 * `credit-mon refresh` systemd unit from $XDG_RUNTIME_DIR. It never touches
 * an API key and never makes network requests, so a bug here cannot leak a
 * credential and a slow provider cannot stall the compositor.
 */

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {displayName, orderedProviders, panelView} from './util.js';
import {menuHeader, pillSegment, pillSeparator, providerCard, updatedRow} from './ui.js';

const SERVICE = 'credit-mon.service';
const RELOAD_SECONDS = 60;

function stateFile() {
    const runtime = GLib.getenv('XDG_RUNTIME_DIR');
    const dir = runtime
        ? GLib.build_filenamev([runtime, 'credit-mon'])
        : GLib.build_filenamev(['/tmp', `credit-mon-${GLib.get_user_name()}`]);
    return GLib.build_filenamev([dir, 'state.json']);
}

/* A system-level poller (root-owned key) writes to /run/credit-mon; fall back to
 * the per-user runtime dir used by install.sh. */
function stateFiles() {
    return ['/run/credit-mon/state.json', stateFile()];
}

function configFile() {
    return GLib.build_filenamev([GLib.get_home_dir(), '.config', 'credit-mon', 'config.json']);
}

function readJsonAsync(path) {
    return new Promise((resolve, reject) => {
        Gio.File.new_for_path(path).load_contents_async(null, (file, res) => {
            try {
                const [, bytes] = file.load_contents_finish(res);
                resolve(JSON.parse(new TextDecoder().decode(bytes)));
            } catch (e) {
                reject(e);
            }
        });
    });
}

function spawn(argv) {
    return Gio.Subprocess.new(
        argv,
        Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE);
}

export const CreditMonitorButton = GObject.registerClass(
class CreditMonitorButton extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Credit Monitor', false);
        this._snapshot = null;
        this._config = {};
        this._refreshing = false;
        this._destroyed = false;

        this._pill = new St.BoxLayout({
            style_class: 'cm-pill cm-pill-ok',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._pill);
        this._setTooltip('Credit Monitor — collecting the first snapshot…');

        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, RELOAD_SECONDS, () => {
            this._reload();
            return GLib.SOURCE_CONTINUE;
        });

        this._render([], 'ok', null);
        this._buildMenu();
        this._reload();
    }

    // ------------------------------------------------------------- data ---

    async _reload() {
        this._snapshot = null;
        for (const path of stateFiles()) {
            try {
                this._snapshot = await readJsonAsync(path);
                break;
            } catch {
                this._snapshot = null;
            }
        }
        try {
            this._config = await readJsonAsync(configFile());
        } catch {
            this._config = {};
        }
        if (this._destroyed)
            return;

        const view = panelView(this._snapshot, this._config, Date.now() / 1000);
        this._render(view.segments, view.state, view.tooltip);
        this._buildMenu();
    }

    _setTooltip(text) {
        if (this.set_tooltip_text)
            this.set_tooltip_text(text);
    }

    _panelProviders() {
        return orderedProviders(this._snapshot, this._config).map(entry => {
            const view = panelView(this._snapshot, this._config, Date.now() / 1000);
            const segment = view.segments.find(s => s.provider === entry.provider) ?? {};
            return {...entry, accent: segment.accent, low: segment.low};
        });
    }

    // ------------------------------------------------------------- panel ---

    _render(segments, state, tooltip) {
        this._pill.destroy_all_children();
        this._pill.style_class = `cm-pill cm-pill-${state}`;

        segments.forEach((segment, index) => {
            if (index > 0)
                this._pill.add_child(pillSeparator());
            this._pill.add_child(pillSegment(segment, this._config));
        });

        if (tooltip)
            this._setTooltip(tooltip);

        try {
            const summary = segments.map(s => `${s.label ?? s.provider} ${s.value}`).join(', ');
            this.accessible_name = summary || 'Credit Monitor';
        } catch {
            /* accessibility hook unavailable */
        }
    }

    // -------------------------------------------------------------- menu ---

    _buildMenu() {
        this.menu.removeAll();
        const now = Date.now() / 1000;

        if (!this._snapshot) {
            this.menu.addMenuItem(menuHeader(null, now));
            const empty = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
            const box = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                style_class: 'cm-col-tight',
                x_expand: true,
            });
            box.add_child(new St.Label({text: 'Waiting for the first snapshot', style_class: 'cm-empty'}));
            box.add_child(new St.Label({
                text: 'systemctl --user start credit-mon.service',
                style_class: 'cm-meta',
            }));
            empty.add_child(box);
            this.menu.addMenuItem(empty);
            this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            this.menu.addMenuItem(this._refreshItem());
            this.menu.addMenuItem(this._actionItem('Diagnostics…', 'emblem-system-symbolic',
                () => this._openTerminal(['credit-mon', 'doctor'])));
            return;
        }

        this.menu.addMenuItem(menuHeader(this._snapshot, now));

        for (const entry of this._panelProviders())
            this.menu.addMenuItem(providerCard(entry, this._config));

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem(''));
        this.menu.addMenuItem(this._refreshItem());

        const dashboards = this._panelProviders().filter(entry => entry.dashboard);
        if (dashboards.length === 1) {
            this.menu.addMenuItem(this._actionItem(
                `Open ${displayName(dashboards[0].provider)}`,
                'external-link-symbolic',
                () => this._openUri(dashboards[0].dashboard)));
        } else if (dashboards.length > 1) {
            const openMenu = new PopupMenu.PopupSubMenuMenuItem('Dashboards');
            for (const entry of dashboards) {
                openMenu.menu.addMenuItem(this._actionItem(
                    displayName(entry.provider), 'external-link-symbolic',
                    () => this._openUri(entry.dashboard)));
            }
            this.menu.addMenuItem(openMenu);
        }

        const keysMenu = new PopupMenu.PopupSubMenuMenuItem('API keys');
        for (const entry of this._panelProviders()) {
            keysMenu.menu.addMenuItem(this._actionItem(
                `Set ${displayName(entry.provider)} key…`, 'dialog-password-symbolic',
                () => this._openTerminal(['credit-mon', 'keys', 'set', entry.provider])));
        }
        keysMenu.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        keysMenu.menu.addMenuItem(this._actionItem('Storage status', 'emblem-locked-symbolic',
            () => this._openTerminal(['credit-mon', 'keys', 'list'])));
        this.menu.addMenuItem(keysMenu);

        this.menu.addMenuItem(this._actionItem('Diagnostics…', 'emblem-system-symbolic',
            () => this._openTerminal(['credit-mon', 'doctor'])));

        this.menu.addMenuItem(updatedRow(this._snapshot));
    }

    _refreshItem() {
        const text = this._refreshing ? 'Refreshing…' : 'Refresh now';
        const item = new PopupMenu.PopupImageMenuItem(text, 'view-refresh-symbolic');
        item.connect('activate', () => this._spawnRefresh());
        if (this._refreshing) {
            item.reactive = false;
            item.add_style_class_name('cm-item-disabled');
        }
        return item;
    }

    _actionItem(text, iconName, callback) {
        const item = new PopupMenu.PopupImageMenuItem(text, iconName);
        item.connect('activate', () => {
            try {
                callback();
            } catch (e) {
                logError(e, 'credit-mon: menu action failed');
            }
        });
        return item;
    }

    // ---------------------------------------------------------- actions ---

    _spawnRefresh() {
        if (this._refreshing)
            return;
        this._refreshing = true;
        this._buildMenu();
        try {
            const proc = spawn(['systemctl', '--user', 'start', SERVICE]);
            proc.wait_check_async(null, (p, res) => {
                this._refreshing = false;
                try {
                    p.wait_check_finish(res);
                } catch (e) {
                    logError(e, 'credit-mon: refresh unit failed');
                }
                this._reload();
            });
        } catch (e) {
            this._refreshing = false;
            logError(e, 'credit-mon: could not start the refresh unit');
            this._reload();
        }
    }

    _openUri(uri) {
        try {
            Gio.AppInfo.launch_default_for_uri(uri, null);
        } catch (e) {
            logError(e, `credit-mon: could not open ${uri}`);
        }
    }

    _openTerminal(argv) {
        const command = 'export PATH="$HOME/.local/bin:$PATH"; ' +
            argv.map(a => `'${a.replace(/'/g, `'\\''`)}'`).join(' ');
        const candidates = [
            ['ptyxis', ['--new-window', '--', 'bash', '-lc', command]],
            ['kgx', ['--', 'bash', '-lc', command]],
            ['gnome-terminal', ['--', 'bash', '-lc', command]],
            ['xterm', ['-e', 'bash', '-lc', command]],
        ];
        for (const [binary, args] of candidates) {
            if (!GLib.find_program_in_path(binary))
                continue;
            try {
                spawn([binary, ...args]);
                return;
            } catch (e) {
                logError(e, `credit-mon: ${binary} failed`);
            }
        }
        Main.notify('Credit Monitor', `No terminal found. Run in a shell:\n${argv.join(' ')}`);
    }

    destroy() {
        this._destroyed = true;
        if (this._timerId) {
            GLib.source_remove(this._timerId);
            this._timerId = 0;
        }
        super.destroy();
    }
});

export default class CreditMonitorExtension extends Extension {
    enable() {
        this._indicator = new CreditMonitorButton();
        Main.panel.addToStatusArea('credit-mon', this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
