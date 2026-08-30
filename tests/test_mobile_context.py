"""Exercise the shipped context functions with browser signals, without network or accounts."""

import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).parents[1] / "src/efvm_monitor/static/app.js"


@pytest.mark.parametrize(
    ("user_agent", "platform", "touch", "ua_mobile", "display", "ios_standalone", "expected"),
    [
        ("Desktop Chrome", "MacIntel", 0, False, False, False, "desktop"),
        ("Desktop Chrome", "Win32", 10, False, True, False, "desktop"),
        ("iPhone", "iPhone", 5, False, False, False, "mobile-browser"),
        ("iPhone", "iPhone", 5, False, False, True, "mobile-pwa"),
        ("Android", "Linux", 5, False, False, False, "mobile-browser"),
        ("Android", "Linux", 5, False, True, False, "mobile-pwa"),
        ("Macintosh Safari", "MacIntel", 5, False, True, False, "mobile-pwa"),
        ("Reduced UA", "Linux", 5, True, False, False, "mobile-browser"),
        ("Other Mobile", "Other", 1, False, False, False, "mobile-browser"),
    ],
)
def test_display_context_and_installation_guidance(
    user_agent: str,
    platform: str,
    touch: int,
    ua_mobile: bool,
    display: bool,
    ios_standalone: bool,
    expected: str,
) -> None:
    import json

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute browser context tests.")
    signals = json.dumps({
        "userAgent": user_agent, "platform": platform, "maxTouchPoints": touch,
        "userAgentData": {"mobile": ua_mobile}, "standalone": ios_standalone,
    })
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
const contextFunctions = source.slice(source.indexOf('function isIos()'),
  source.indexOf('function notificationPermission()'));
const installationFunction = source.slice(
  source.indexOf('function configureInstallationExperience()'),
  source.indexOf('function urlBase64ToUint8Array('));
const document = {body: {dataset: {}}};
const elements = Object.fromEntries(['browserIntro', 'pwaIntro', 'onboarding', 'monitorsEmpty',
  'iosInstallHelp', 'androidInstallHelp', 'desktopInstallHelp', 'installApp', 'installTitle',
  'installStatus'].map(key => [key, {hidden:false, textContent:''}]));
const defaultMonitorsEmptyText = 'Preencha o Passo 1';
let appInstalled = false;
let deferredInstallPrompt = null;
function updateOnboarding() {}
const window = {navigator: SIGNALS, matchMedia: () => ({matches: DISPLAY})};
eval(contextFunctions + installationFunction);
configureInstallationExperience();
assert.equal(document.body.dataset.displayContext, EXPECTED);
assert.equal(elements.pwaIntro.hidden, EXPECTED !== 'mobile-pwa');
assert.equal(elements.browserIntro.hidden, EXPECTED === 'mobile-pwa');
assert.equal(elements.onboarding.hidden, EXPECTED === 'mobile-pwa');
assert.equal(elements.monitorsEmpty.textContent.includes('Passo 1'), EXPECTED === 'desktop');
if (EXPECTED === 'mobile-browser' && /iPhone|Android/.test(window.navigator.userAgent)) {
  // Installation in another window must not hide the tutorial in a browser tab.
  appInstalled = true;
  configureInstallationExperience();
  assert.equal(document.body.dataset.displayContext, 'mobile-browser');
  assert.equal(elements.onboarding.hidden, false);
  assert.equal(/iPhone/.test(window.navigator.userAgent)
    ? elements.iosInstallHelp.hidden : elements.androidInstallHelp.hidden, false);
}
// Returning from standalone must restore the original browser experience.
window.navigator.standalone = false;
window.matchMedia = () => ({matches:false});
configureInstallationExperience();
assert.equal(elements.onboarding.hidden, false);
assert.equal(elements.browserIntro.hidden, false);
assert.equal(elements.pwaIntro.hidden, true);
"""
    script = (script.replace("SIGNALS", signals).replace("DISPLAY", json.dumps(display))
              .replace("EXPECTED", json.dumps(expected)))
    result = subprocess.run([node, "-e", script, str(APP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
