/* Pure display logic — no GNOME Shell imports, so it can be unit tested.
 * Loaded both by the extension (relative ESM import) and by tests/extension-test.js.
 */

export const SHORT_NAME = {deepseek: 'DS', moonshot: 'Kimi', zai: 'Z.ai'};
export const DISPLAY_NAME = {deepseek: 'DeepSeek', moonshot: 'Moonshot AI', zai: 'Z.ai'};
export const ACCENT = {deepseek: '#4d6bfe', moonshot: '#c084fc', zai: '#2dd4bf'};

const SYMBOLS = {USD: '$', CNY: '¥', EUR: '€', GBP: '£'};

export function shortName(provider) {
    return SHORT_NAME[provider] ?? String(provider).slice(0, 3).toUpperCase();
}

export function displayName(provider) {
    return DISPLAY_NAME[provider] ?? provider;
}

export function accentFor(config, provider) {
    const value = (config?.panel?.accents ?? {})[provider];
    return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)
        ? value
        : (ACCENT[provider] ?? '#8ab4f8');
}

export function money(amount, currency) {
    if (amount === null || amount === undefined || Number.isNaN(amount))
        return '—';
    const magnitude = Math.abs(Number(amount));
    const digits = magnitude > 0 && magnitude < 0.1 ? 3 : 2;
    const text = Number(amount).toFixed(digits);
    const symbol = SYMBOLS[String(currency ?? '').toUpperCase()];
    return symbol ? `${symbol}${text}` : `${text} ${currency ?? ''}`.trim();
}

/** Spend is always shown as a negative delta: "−$0.36". */
export function signedMoney(amount, currency) {
    if (amount === null || amount === undefined || Number.isNaN(amount))
        return null;
    const text = money(Math.abs(amount), currency);
    return amount > 0 ? `−${text}` : text;
}

export function pctText(percent) {
    if (percent === null || percent === undefined)
        return '—';
    return `${Math.round(Number(percent))}%`;
}

export function clockTime(epochSeconds) {
    if (!epochSeconds)
        return '—';
    return new Date(epochSeconds * 1000)
        .toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
}

export function agoText(seconds) {
    if (seconds < 90)
        return 'just now';
    if (seconds < 5400)
        return `${Math.round(seconds / 60)} min ago`;
    if (seconds < 172800)
        return `${Math.round(seconds / 3600)} h ago`;
    return `${Math.round(seconds / 86400)} d ago`;
}

export function lowThreshold(config, provider) {
    const value = (config?.panel?.low_balance ?? {})[provider];
    return typeof value === 'number' ? value : null;
}

export function isLow(config, entry) {
    const threshold = lowThreshold(config, entry.provider);
    if (threshold === null || entry.total === null || entry.total === undefined)
        return false;
    return entry.total < threshold;
}

export function orderedProviders(snapshot, config) {
    const providers = snapshot?.providers ?? [];
    const order = config?.panel?.order;
    if (!Array.isArray(order) || order.length === 0)
        return [...providers];
    const rank = p => {
        const i = order.indexOf(p.provider);
        return i === -1 ? 99 : i;
    };
    return [...providers].sort((a, b) => rank(a) - rank(b));
}

/**
 * Everything the panel needs to render: one segment per provider plus the
 * aggregate state and tooltip.
 */
export function panelView(snapshot, config, nowSeconds) {
    const showNames = config?.panel?.show_provider_names ?? true;

    if (!snapshot) {
        return {
            segments: [],
            text: '···',
            state: 'ok',
            tooltip: 'Credit Monitor — no snapshot yet.\n' +
                'Run: systemctl --user start credit-mon.service\n' +
                'or open the menu → Refresh now.',
            stale: false,
            age: 0,
        };
    }

    const entries = orderedProviders(snapshot, config);
    const segments = [];
    const tooltipLines = [];
    let state = 'ok';

    for (const entry of entries) {
        const low = isLow(config, entry);
        const accent = accentFor(config, entry.provider);
        if (!entry.ok) {
            if (state !== 'error')
                state = 'error';
            segments.push({
                provider: entry.provider,
                label: showNames ? shortName(entry.provider) : null,
                value: '—',
                accent,
                low: false,
                ok: false,
            });
            tooltipLines.push(`${displayName(entry.provider)}: ${entry.error ?? 'unavailable'}`);
            continue;
        }
        if (low && state !== 'error')
            state = 'low';
        const value = money(entry.total, entry.currency);
        segments.push({
            provider: entry.provider,
            label: showNames ? shortName(entry.provider) : null,
            value,
            accent,
            low,
            ok: true,
        });
        let line = `${displayName(entry.provider)}: ${value}`;
        if (entry.spend_today !== null && entry.spend_today !== undefined)
            line += ` · ${signedMoney(entry.spend_today, entry.currency)} today`;
        tooltipLines.push(line);
    }

    if (segments.length === 0) {
        segments.push({provider: 'none', label: null, value: 'set up', accent: '#e01b24', low: false, ok: false});
        state = 'error';
    }

    const age = nowSeconds - (snapshot.generated_at ?? 0);
    const staleAfter = Math.max(3 * (snapshot.refresh_seconds ?? 600), 1800);
    const stale = age > staleAfter;
    if (stale && state === 'ok')
        state = 'stale';

    tooltipLines.push(stale ? `data ${agoText(age)} (stale)` : `updated ${agoText(age)}`);

    const text = segments
        .map(s => (s.label ? `${s.label} ${s.value}` : s.value))
        .join('  │  ');

    return {segments, text, state, tooltip: tooltipLines.join('\n'), stale, age};
}
