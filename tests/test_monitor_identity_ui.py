"""Exercise shipped UI functions: private identifiers stay in actions, not visible text."""

import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parents[1] / "src/efvm_monitor/static/app.js"


@pytest.mark.parametrize(
    "scenario",
    [
        "card", "confirmation", "cancel", "pause", "resume", "remove", "history",
        "not-found", "already-running", "unrelated-error", "network-error",
        "expired", "today", "hours",
    ],
)
def test_monitor_identity_presentation_and_action_target(scenario: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute UI tests.")
    script = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(process.argv[1], 'utf8');
const scenario = process.argv[2];
class Date extends globalThis.Date {
  constructor(...args) { super(...(args.length ? args : ['2026-08-31T02:59:59Z'])); }
  static now() { return new Date().getTime(); }
}
// A minimal DOM records rendered text, accessibility labels and actual click handlers.
class Element {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.events = {};
    this.classList = {add: () => {}};
    this.ownText = '';
  }
  set textContent(value) { this.ownText = value; this.children = []; }
  get textContent() { return this.ownText + this.children.map(x => x.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.ownText = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, callback) { this.events[event] = callback; }
  scrollIntoView() { this.scrolled = true; }
}
const document = {createElement: () => new Element()};
const elements = Object.fromEntries(['monitorsList', 'monitorsEmpty', 'dashboardState',
  'statusBadge', 'currentMonitorDetail', 'historyList', 'historyEmpty', 'historySection',
  'formError'].map(key => [key, new Element()]));
const navigator = {onLine: true};
let confirmation;
const window = {confirm: text => { confirmation = text; return scenario !== 'cancel'; }};
const firstId = 765432;
const targetId = 984321;
const stationsById = new Map([['1', 'Belo Horizonte'], ['2', 'Dois Irmãos']]);
let selectedMonitorId = targetId;
let currentMonitors = [];
let renderedState;
let refreshes = 0;
const calls = [];
const history = [{id: targetId, monitoring_id: targetId, result: 'SEM_VAGA',
  message: `Internal monitor ${targetId}`, checked_at: '2026-08-30T12:00:00Z'}];
const errors = {
  'not-found': new Error(`Monitoramento ${targetId} não encontrado.`),
  'already-running': new Error(`O monitoramento ${targetId} já está ativo.`),
  'unrelated-error': new Error('O limite de 10 monitoramentos foi atingido.'),
  'network-error': new TypeError('Failed to fetch'),
};
async function fetchJson(url, options = {}) {
  calls.push({url, method: options.method || 'GET'});
  if (errors[scenario]) throw errors[scenario];
  return {monitoring_id: targetId, items: history};
}
async function refreshState() { refreshes += 1; }
function renderState(state) { renderedState = state.monitoring_id; }
function updateOnboarding() {}
// Execute the real functions, rather than reproducing routing or rendering logic.
const names = ['setFormError', 'userFacingError', 'statusClass', 'statusLabel', 'isTravelExpired',
  'friendlyStatusMessage', 'stationName', 'formatTravelDate', 'formatInterval',
  'formatDateTime', 'nextCheckMessage', 'monitorAction', 'renderMonitors', 'renderHistory',
  'loadHistory',
  'pauseMonitor', 'resumeMonitor', 'removeMonitor'];
eval(names.map(name => {
  const match = source.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n}'));
  assert.ok(match, `Missing shipped function: ${name}`);
  return match[0];
}).join('\n'));
const state = (id, running) => ({monitoring_id: id, running, status: 'SEM_VAGA',
  query: {origin: '1', destination: '2', travel_date: scenario === 'expired' ? '2026-08-29'
    : scenario === 'today' ? '2026-08-30' : '2026-09-15',
    travel_class: 'Econômica', check_interval_seconds: 60}});
renderMonitors([state(firstId, true), state(targetId, scenario !== 'resume')]);
const cards = elements.monitorsList.children;
function assertNoVisibleIds(element) {
  for (const id of [firstId, targetId]) {
    assert.ok(!element.textContent.includes(String(id)));
    assert.ok(!Object.values(element.attributes).some(value => value.includes(String(id))));
  }
  element.children.forEach(assertNoVisibleIds);
}
cards.forEach(card => {
  assert.equal(card.children[0].children[0].textContent, 'MONITORAMENTO');
  assertNoVisibleIds(card);
});
(async () => {
  if (scenario === 'hours') {
    assert.equal(formatInterval(300), '5 minutos');
    assert.equal(formatInterval(900), '15 minutos');
    assert.equal(formatInterval(3600), '1 hora');
    assert.equal(formatInterval(10800), '3 horas');
    return;
  }
  if (['expired', 'today'].includes(scenario)) {
    const labels = cards[1].children.at(-1).children.map(x => x.textContent);
    assert.ok(labels.includes('Remover'));
    assert.ok(labels.includes('Ver histórico'));
    assert.equal(labels.includes('Pausar'), scenario === 'today');
    assert.equal(labels.includes('Retomar'), false);
    assert.equal(isTravelExpired(state(targetId, true)), scenario === 'expired');
    if (scenario === 'expired') {
      assert.equal(cards[1].dataset.state, 'parado');
      assert.ok(cards[1].textContent.includes('Essa viagem já expirou. Deseja remover?'));
      assert.equal(statusLabel(state(targetId, true)), 'Expirado');
      assert.equal(nextCheckMessage(state(targetId, true)),
        'Próxima verificação: viagem expirada.');
    }
    const historyButton = cards[1].children.at(-1).children.find(
      x => x.textContent === 'Ver histórico');
    await historyButton.events.click();
    assert.equal(calls[0].url, `/api/monitoramentos/${targetId}/historico?limit=10`);
    assert.ok(elements.historyList.textContent.includes('Nenhuma vaga encontrada'));
    return;
  }
  if (scenario === 'card') return;
  const label = scenario === 'history' ? 'Ver histórico'
    : scenario === 'resume' ? 'Retomar'
    : ['confirmation', 'cancel', 'remove'].includes(scenario) ? 'Remover' : 'Pausar';
  const button = cards[1].children.at(-1).children.find(x => x.textContent === label);
  assert.ok(button);
  await button.events.click();
  assert.equal(button.disabled, false);
  if (label === 'Remover') {
    assert.equal(confirmation,
      'Remover este monitoramento? Ele deixará de aparecer na sua conta.');
    assert.ok(!/\d/.test(confirmation));
  }
  if (scenario === 'cancel') {
    assert.deepEqual(calls, []);
    assert.equal(refreshes, 0);
    assert.equal(selectedMonitorId, targetId);
    return;
  }
  const suffix = label === 'Ver histórico' ? '/historico?limit=10'
    : label === 'Pausar' ? '/pausar' : label === 'Retomar' ? '/retomar' : '';
  assert.deepEqual(calls, [{url: `/api/monitoramentos/${targetId}${suffix}`,
    method: label === 'Ver histórico' ? 'GET' : label === 'Remover' ? 'DELETE' : 'POST'}]);
  if (errors[scenario]) {
    const expected = {
      'not-found': 'Monitoramento não encontrado.',
      'already-running': 'O monitoramento já está ativo.',
      'unrelated-error': 'O limite de 10 monitoramentos foi atingido.',
      'network-error': 'Não foi possível pausar esta viagem agora.',
    };
    assert.equal(elements.formError.textContent, expected[scenario]);
    assert.equal(refreshes, 0);
    if (['not-found', 'already-running'].includes(scenario)) {
      assert.ok(errors[scenario].message.includes(String(targetId)), 'Original error stays intact');
    }
  } else if (scenario === 'history') {
    assert.equal(selectedMonitorId, targetId);
    assert.equal(renderedState, targetId);
    assert.equal(elements.historySection.dataset.monitoringId, String(targetId));
    assert.equal(elements.historySection.scrolled, true);
    assert.ok(elements.historyList.textContent.includes('Nenhuma vaga encontrada'));
    assertNoVisibleIds(elements.historyList);
  } else {
    assert.equal(refreshes, 1);
    assert.equal(selectedMonitorId, label === 'Remover' ? null : targetId);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", script, str(APP_JS), scenario], capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
