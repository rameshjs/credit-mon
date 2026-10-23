// Unit tests for the pure display logic. Run: gjs -m tests/extension-test.js
import * as util from '../extension/util.js';

let failures = 0;
function check(name, actual, expected) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a === e) {
        print(`  ok   ${name}`);
    } else {
        failures++;
        print(`  FAIL ${name}\n       expected ${e}\n       actual   ${a}`);
    }
}

const NOW = 1760000000;

const healthy = {
    generated_at: NOW - 120,
    refresh_seconds: 600,
    providers: [
        {provider: 'deepseek', ok: true, total: 15.42, currency: 'USD',
            rows: [['Balance', '$15.42']], spend_today: 0.36, error: null},
        {provider: 'zai', ok: true, total: 42.5, currency: 'CNY',
            rows: [['Available', '¥42.50']], spend_today: null, error: null},
    ],
};

const config = {panel: {show_provider_names: true, low_balance: {deepseek: 5.0, zai: 10.0},
    accents: {deepseek: '#4d6bfe', zai: '#2dd4bf'}}};

print('money()');
check('usd', util.money(15.42, 'USD'), '$15.42');
check('cny symbol', util.money(42.5, 'CNY'), '¥42.50');
check('sub-cent precision', util.money(0.0412, 'USD'), '$0.041');
check('unknown currency', util.money(3, 'CHF'), '3.00 CHF');
check('null', util.money(null, 'USD'), '—');
check('zero stays 2dp', util.money(0, 'USD'), '$0.00');

print('money helpers');
check('signed negative', util.signedMoney(0.36, 'USD'), '−$0.36');
check('signed null', util.signedMoney(null, 'USD'), null);
check('pct rounds', util.pctText(14.6), '15%');
check('pct missing', util.pctText(null), '—');
check('accent from config', util.accentFor(config, 'zai'), '#2dd4bf');
check('accent default', util.accentFor({}, 'deepseek'), '#4d6bfe');
check('accent unknown provider', util.accentFor({}, 'nope'), '#8ab4f8');
check('accent rejects junk', util.accentFor({panel: {accents: {zai: 'red'}}}, 'zai'), '#2dd4bf');
check('display name', util.displayName('deepseek'), 'DeepSeek');

print('panelView()');
check('healthy text', util.panelView(healthy, config, NOW).text, 'DS $15.42  │  Z.ai ¥42.50');
check('healthy state', util.panelView(healthy, config, NOW).state, 'ok');
check('no names', util.panelView(healthy, {panel: {show_provider_names: false}}, NOW).text,
    '$15.42  │  ¥42.50');
const view = util.panelView(healthy, config, NOW);
check('segments count', view.segments.length, 2);
check('segment label', view.segments[0].label, 'DS');
check('segment value', view.segments[0].value, '$15.42');
check('segment accent', view.segments[1].accent, '#2dd4bf');
check('segment low flag', view.segments[0].low, false);
const lowView = util.panelView(healthy,
    {panel: {show_provider_names: true, low_balance: {deepseek: 20.0, zai: 10.0}}}, NOW);
check('low segment flagged', lowView.segments[0].low, true);
check('low state', lowView.state, 'low');

const lowConfig = {panel: {show_provider_names: true, low_balance: {deepseek: 20.0, zai: 10.0}}};
check('low balance trips state', util.panelView(healthy, lowConfig, NOW).state, 'low');
check('exactly at threshold is not low',
    util.panelView({...healthy, providers: [{provider: 'deepseek', ok: true, total: 5.0, currency: 'USD'}]},
        config, NOW).state, 'ok');

const failed = {generated_at: NOW, refresh_seconds: 600, providers: [
    {provider: 'deepseek', ok: false, error: 'no API key stored', total: null},
    {provider: 'zai', ok: true, total: 42.5, currency: 'CNY'},
]};
check('error text', util.panelView(failed, config, NOW).text, 'DS —  │  Z.ai ¥42.50');
check('error state wins over low', util.panelView(failed, config, NOW).state, 'error');
check('error in tooltip', util.panelView(failed, config, NOW).tooltip.includes('no API key stored'), true);

check('missing snapshot', util.panelView(null, config, NOW).text, '···');
check('empty providers', util.panelView({generated_at: NOW, providers: []}, config, NOW).text, 'set up');

const stale = {...healthy, generated_at: NOW - 4000, refresh_seconds: 600};
check('stale state', util.panelView(stale, config, NOW).state, 'stale');
check('fresh is not stale', util.panelView(healthy, config, NOW).stale, false);

print('ordering');
const reordered = {...healthy, providers: [healthy.providers[1], healthy.providers[0]]};
check('config order applied',
    util.orderedProviders(reordered, {panel: {order: ['deepseek', 'zai']}}).map(p => p.provider),
    ['deepseek', 'zai']);

print('agoText()');
check('just now', util.agoText(30), 'just now');
check('minutes', util.agoText(600), '10 min ago');
check('hours', util.agoText(7200), '2 h ago');
check('days', util.agoText(259200), '3 d ago');

print('shortName()');
check('deepseek', util.shortName('deepseek'), 'DS');
check('zai', util.shortName('zai'), 'Z.ai');
check('unknown', util.shortName('anthropic'), 'ANT');

print(failures === 0 ? '\nALL PASSED' : `\n${failures} FAILURE(S)`);
if (failures > 0)
    imports.system.exit(1);
