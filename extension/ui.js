/* Widget builders for the panel pill and the dropdown cards.
 * Kept apart from extension.js so the menu/dashboard layout stays readable.
 */

import Cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

import {agoText, clockTime, money, pctText, signedMoney, displayName} from './util.js';

const CARD_WIDTH = 320;

// ---------------------------------------------------------------- helpers ---

export function hexToRgb(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
    if (!m)
        return [0.55, 0.7, 1.0];
    const int = parseInt(m[1], 16);
    return [((int >> 16) & 0xff) / 255, ((int >> 8) & 0xff) / 255, (int & 0xff) / 255];
}

function rgba(hex, alpha) {
    const [r, g, b] = hexToRgb(hex);
    return `rgba(${Math.round(r * 255)},${Math.round(g * 255)},${Math.round(b * 255)},${alpha})`;
}

function label(text, styleClass, props = {}) {
    return new St.Label({
        text,
        style_class: styleClass,
        y_align: Clutter.ActorAlign.CENTER,
        ...props,
    });
}

function spacer() {
    return new St.Widget({x_expand: true});
}

function dot(accent, size = 8) {
    return new St.Widget({
        style: `background-color: ${accent}; border-radius: ${size / 2}px;`,
        width: size,
        height: size,
        y_align: Clutter.ActorAlign.CENTER,
    });
}

function hbox(styleClass = 'cm-row') {
    return new St.BoxLayout({
        style_class: styleClass,
        y_align: Clutter.ActorAlign.CENTER,
    });
}

function vbox(styleClass = 'cm-col') {
    return new St.BoxLayout({
        orientation: Clutter.Orientation.VERTICAL,
        style_class: styleClass,
        x_expand: true,
    });
}

function divider(styleClass = 'cm-divider') {
    return new St.Widget({style_class: styleClass, x_expand: true, height: 1});
}

// ------------------------------------------------------------ panel pill ---

/** One provider segment: "● DS $15.42" */
export function pillSegment(segment, config) {
    const box = hbox('cm-seg');
    box.add_child(dot(segment.accent, 8));
    if (segment.label)
        box.add_child(label(segment.label, 'cm-name'));
    const valueClass = segment.low ? 'cm-value cm-value-low' : 'cm-value';
    box.add_child(label(segment.value, valueClass));
    return box;
}

export function pillSeparator() {
    return new St.Widget({style_class: 'cm-divider-v', width: 1, height: 11});
}

// --------------------------------------------------------------- sparkline ---

function paintSpark(cr, width, height, points, accent) {
    const values = points.map(p => Number(p[1]));
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (max - min < 1e-9)
        max = min + 1;
    const pad = 3;
    const xFor = i => (i / (points.length - 1)) * (width - 1);
    const yFor = v => height - pad - ((v - min) / (max - min)) * (height - pad * 2);
    const [r, g, b] = hexToRgb(accent);

    const gradient = new Cairo.LinearGradient(0, 0, 0, height);
    gradient.addColorStopRGBA(0, r, g, b, 0.32);
    gradient.addColorStopRGBA(1, r, g, b, 0.02);
    cr.setSource(gradient);
    cr.moveTo(0, height);
    points.forEach((p, i) => cr.lineTo(xFor(i), yFor(Number(p[1]))));
    cr.lineTo(xFor(points.length - 1), height);
    cr.closePath();
    cr.fill();

    cr.setSourceRGBA(r, g, b, 0.95);
    cr.setLineWidth(1.6);
    try {
        cr.setLineJoin(Cairo.LineJoin.ROUND);
        cr.setLineCap(Cairo.LineCap.ROUND);
    } catch {
        /* older cairo bindings */
    }
    points.forEach((p, i) => {
        const x = xFor(i);
        const y = yFor(Number(p[1]));
        if (i === 0)
            cr.moveTo(x, y);
        else
            cr.lineTo(x, y);
    });
    cr.stroke();

    cr.arc(xFor(points.length - 1), yFor(values[values.length - 1]), 2.2, 0, 2 * Math.PI);
    cr.fill();
}

export function sparkline(points, accent, height = 26) {
    const area = new St.DrawingArea({
        style_class: 'cm-spark',
        height,
        x_expand: true,
    });
    area.connect('repaint', () => {
        const cr = area.get_context();
        try {
            const [w, h] = area.get_surface_size();
            if (w > 2 && h > 2 && Array.isArray(points) && points.length > 1)
                paintSpark(cr, w, h, points, accent);
        } catch (e) {
            logError(e, 'credit-mon: sparkline paint failed');
        } finally {
            cr.$dispose();
        }
    });
    return area;
}

// -------------------------------------------------------------- menu cards ---

function kvRow(labelText, valueText, opts = {}) {
    const row = hbox('cm-kv');
    const left = label(labelText, 'cm-kv-label');
    if (opts.ellipsize)
        left.clutter_text.ellipsize = Pango.EllipsizeMode.END;
    row.add_child(left);
    row.add_child(spacer());
    row.add_child(label(valueText, opts.valueClass ?? 'cm-kv-value'));
    return row;
}

function quotaRow(quota, accent) {
    const row = hbox('cm-kv');
    row.add_child(label(quota.label, 'cm-kv-label'));

    const track = new St.Widget({style_class: 'cm-track', x_expand: true, height: 4});
    const fill = new St.Widget({
        style: `background-color: ${rgba(accent, 0.95)}; border-radius: 2px;`,
        height: 4,
        width: 0,
    });
    track.add_child(fill);
    const percent = Math.max(0, Math.min(100, Number(quota.percent ?? 0)));
    track.connect('notify::width', () => {
        fill.set_width(Math.max(0, Math.round((track.width * percent) / 100)));
    });
    row.add_child(track);

    const value = pctText(quota.percent);
    row.add_child(label(value, 'cm-kv-value cm-kv-num'));
    if (quota.reset)
        row.add_child(label(quota.reset, 'cm-kv-reset'));
    else if (quota.detail)
        row.add_child(label(quota.detail, 'cm-kv-reset'));
    return row;
}

/** A provider card: title, big balance, delta chip, sparkline, metrics. */
export function providerCard(entry, config) {
    const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    item.add_style_class_name('cm-card-item');

    const card = vbox('cm-card');
    card.set_width(CARD_WIDTH);

    // title row
    const titleRow = hbox('cm-row');
    titleRow.add_child(dot(entry.accent ?? '#8ab4f8', 8));
    titleRow.add_child(label(displayName(entry.provider), 'cm-card-title'));
    titleRow.add_child(spacer());
    if (entry.currency)
        titleRow.add_child(label(String(entry.currency).toUpperCase(), 'cm-currency'));
    card.add_child(titleRow);

    if (!entry.ok) {
        const errorRow = hbox('cm-row-flush');
        const message = label(entry.error ?? 'unavailable', 'cm-error', {x_expand: true});
        message.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        errorRow.add_child(message);
        card.add_child(errorRow);
        for (const warning of entry.warnings ?? [])
            card.add_child(label(warning, 'cm-warn'));
        item.add_child(card);
        return item;
    }

    // balance + delta chip
    const amountRow = hbox('cm-row');
    amountRow.add_child(label(money(entry.total, entry.currency), 'cm-amount'));

    const chips = [];
    if (entry.spend_today !== null && entry.spend_today !== undefined && entry.spend_today > 0) {
        chips.push(label(`${signedMoney(entry.spend_today, entry.currency)} today`,
            'cm-chip cm-chip-spend'));
    }
    if (chips.length) {
        amountRow.add_child(spacer());
        const chipBox = hbox('cm-row-tight');
        for (const chip of chips)
            chipBox.add_child(chip);
        amountRow.add_child(chipBox);
    }
    card.add_child(amountRow);

    if (Array.isArray(entry.spark) && entry.spark.length > 1) {
        card.add_child(sparkline(entry.spark, entry.accent ?? '#8ab4f8', 26));
    }

    const metrics = [];
    for (const quota of entry.quota ?? [])
        card.add_child(quotaRow(quota, entry.accent ?? '#8ab4f8'));

    // Detail rows, minus the ones already shown as a big number or as bars.
    const shown = new Set((entry.quota ?? []).map(q => q.label));
    const skip = /^(balance|available)$/i;
    for (const [key, value] of entry.rows ?? []) {
        if (shown.has(key) || skip.test(key))
            continue;
        if (/^[\D]*0\.0+$/.test(String(value)))  // "$0.00" carries no information
            continue;
        if (metrics.length >= 2)
            break;
        metrics.push(key);
        card.add_child(kvRow(key, value));
    }

    const meta = [];
    if (entry.burn_per_day)
        meta.push(`${money(entry.burn_per_day, entry.currency)}/day`);
    if (entry.days_left !== null && entry.days_left !== undefined)
        meta.push(`~${Math.round(entry.days_left)} d left`);
    if (meta.length) {
        card.add_child(divider('cm-divider-card'));
        card.add_child(label(meta.join('  ·  '), 'cm-meta'));
    }

    for (const warning of entry.warnings ?? [])
        card.add_child(label(`⚠ ${warning}`, 'cm-warn'));

    item.add_child(card);
    return item;
}

/** Small "Credits ... updated X" header above the cards. */
export function menuHeader(snapshot, nowSeconds) {
    const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    item.add_style_class_name('cm-header-item');
    const row = hbox('cm-header-row');
    row.add_child(label('CREDITS', 'cm-header'));
    row.add_child(spacer());
    const age = nowSeconds - (snapshot?.generated_at ?? 0);
    const text = snapshot ? `updated ${agoText(age)}` : 'no data';
    row.add_child(label(text, 'cm-header-meta'));
    item.add_child(row);
    return item;
}

export function updatedRow(snapshot) {
    const item = new PopupMenu.PopupBaseMenuItem({reactive: false, can_focus: false});
    item.add_style_class_name('cm-header-item');
    const row = hbox('cm-footer-row');
    const stamp = snapshot?.generated_at ? clockTime(snapshot.generated_at) : '—';
    row.add_child(label(`Snapshot ${stamp}`, 'cm-footer'));
    item.add_child(row);
    return item;
}
