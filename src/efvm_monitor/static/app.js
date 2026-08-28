"use strict";

const elements = {
  form: document.querySelector("#monitor-form"),
  origin: document.querySelector("#origin"),
  destination: document.querySelector("#destination"),
  travelDate: document.querySelector("#travel-date"),
  travelClass: document.querySelector("#travel-class"),
  passengers: document.querySelector("#passengers"),
  interval: document.querySelector("#interval"),
  smsAlerts: document.querySelector("#sms-alerts"),
  whatsappAlerts: document.querySelector("#whatsapp-alerts"),
  pushStatus: document.querySelector("#push-status"),
  activatePush: document.querySelector("#activate-push"),
  testPush: document.querySelector("#test-push"),
  disablePush: document.querySelector("#disable-push"),
  installApp: document.querySelector("#install-app"),
  iosInstallHelp: document.querySelector("#ios-install-help"),
  startButton: document.querySelector("#start-button"),
  stopButton: document.querySelector("#stop-button"),
  catalogState: document.querySelector("#catalog-state"),
  formError: document.querySelector("#form-error"),
  statusBadge: document.querySelector("#status-badge"),
  statusIcon: document.querySelector("#status-icon"),
  statusMessage: document.querySelector("#status-message"),
  lastCheck: document.querySelector("#last-check"),
  monitorsEmpty: document.querySelector("#monitors-empty"),
  monitorsList: document.querySelector("#monitors-list"),
  currentMonitorDetail: document.querySelector("#current-monitor-detail"),
  querySummary: document.querySelector("#query-summary"),
  summaryRoute: document.querySelector("#summary-route"),
  summaryDateClass: document.querySelector("#summary-date-class"),
  summaryInterval: document.querySelector("#summary-interval"),
  summaryAlerts: document.querySelector("#summary-alerts"),
  refreshHistory: document.querySelector("#refresh-history"),
  historyEmpty: document.querySelector("#history-empty"),
  historyList: document.querySelector("#history-list"),
};

let catalogReady = false;
let stationsById = new Map();
let currentQuery = null;
let selectedMonitorId = null;
let currentMonitors = [];
let pushRegistration = null;
let pushConfig = null;
let pushSubscribed = false;
let deferredInstallPrompt = null;
const pushDeviceId = getPushDeviceId();

function getPushDeviceId() {
  const storageKey = "efvm-push-device-id";
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (saved) return saved;
    const generated = window.crypto?.randomUUID?.() || `device-${Date.now()}-${Math.random()}`;
    window.localStorage.setItem(storageKey, generated);
    return generated;
  } catch (_error) {
    return `device-${Date.now()}-${Math.random()}`;
  }
}

function apiErrorMessage(body, fallback) {
  if (typeof body?.detail === "string") return body.detail;
  if (Array.isArray(body?.detail) && body.detail.length > 0) {
    return body.detail.map((item) => item.msg).join(" ");
  }
  return fallback;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(apiErrorMessage(body, "A solicitação não foi concluída."));
  return body;
}

function setFormError(message = "") {
  elements.formError.textContent = message;
  elements.formError.hidden = !message;
}

function option(value, label) {
  const item = document.createElement("option");
  item.value = String(value);
  item.textContent = label;
  return item;
}

function populateSelect(select, items, placeholder) {
  select.replaceChildren(option("", placeholder));
  items.forEach((item) => select.append(option(item.id, item.name)));
}

function localDateValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function selectDefault(select, items, expectedName) {
  const match = items.find((item) => item.name === expectedName);
  if (match) select.value = String(match.id);
}

async function loadCatalog() {
  try {
    const catalog = await fetchJson("/api/catalogo");
    stationsById = new Map(catalog.stations.map((station) => [String(station.id), station.name]));
    populateSelect(elements.origin, catalog.stations, "Selecione a origem");
    populateSelect(elements.destination, catalog.stations, "Selecione o destino");
    populateSelect(elements.travelClass, catalog.classes, "Selecione a classe");

    selectDefault(elements.origin, catalog.stations, "Belo Horizonte");
    selectDefault(
      elements.destination,
      catalog.stations,
      "Pedro Nolasco (Cariacica / Vitória)",
    );
    selectDefault(elements.travelClass, catalog.classes, "Econômica");

    const maximumDate = new Date();
    maximumDate.setDate(maximumDate.getDate() + catalog.sale_window_days);
    elements.travelDate.min = document.body.dataset.minimumDate;
    elements.travelDate.max = localDateValue(maximumDate);

    catalogReady = true;
    elements.catalogState.textContent = `${catalog.stations.length} estações disponíveis`;
    elements.catalogState.className = "catalog-state ready";
    setControls(false);
  } catch (error) {
    elements.catalogState.textContent = "Catálogo indisponível";
    elements.catalogState.className = "catalog-state error";
    setFormError(error.message);
  }
}

function setControls(_running = false) {
  const formControls = [
    elements.origin,
    elements.destination,
    elements.travelDate,
    elements.travelClass,
    elements.interval,
  ];
  formControls.forEach((control) => {
    control.disabled = !catalogReady;
  });
  elements.whatsappAlerts.disabled =
    elements.whatsappAlerts.dataset.configured !== "true";
  elements.smsAlerts.disabled = elements.smsAlerts.dataset.configured !== "true";
  elements.passengers.disabled = true;
  elements.startButton.disabled = !catalogReady;
  elements.stopButton.disabled = true;
}

function statusClass(status) {
  return `status-${status.toLowerCase().replaceAll("_", "-")}`;
}

function statusIcon(status) {
  return {
    PARADO: "—",
    AGUARDANDO: "…",
    ENCERRANDO: "…",
    TEM_VAGA: "✓",
    SEM_VAGA: "×",
    ERRO: "!",
  }[status] || "?";
}

function stationName(id) {
  return stationsById.get(String(id)) || String(id);
}

function formatTravelDate(value) {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function applyQueryToForm(query) {
  if (!catalogReady || !query) return;
  elements.origin.value = String(query.origin);
  elements.destination.value = String(query.destination);
  elements.travelDate.value = query.travel_date;
  elements.interval.value = String(query.check_interval_seconds);
  if (elements.whatsappAlerts.dataset.configured === "true") {
    elements.whatsappAlerts.checked = Boolean(query.whatsapp_enabled);
  }
  if (elements.smsAlerts.dataset.configured === "true") {
    elements.smsAlerts.checked = Boolean(query.sms_enabled);
  }
  const classOption = Array.from(elements.travelClass.options).find(
    (item) => item.text === query.travel_class,
  );
  if (classOption) elements.travelClass.value = classOption.value;
}

function renderQuery(query) {
  currentQuery = query;
  elements.querySummary.hidden = !query;
  if (!query) return;
  elements.summaryRoute.textContent = `${stationName(query.origin)} → ${stationName(query.destination)}`;
  elements.summaryDateClass.textContent = `${formatTravelDate(query.travel_date)} · ${query.travel_class} · 1 passageiro`;
  elements.summaryInterval.textContent = `${query.check_interval_seconds} segundos`;
  const alertChannels = [];
  if (pushSubscribed) alertChannels.push("Web Push (principal)");
  if (query.sms_enabled) alertChannels.push("SMS");
  if (query.whatsapp_enabled) alertChannels.push("WhatsApp");
  elements.summaryAlerts.textContent = alertChannels.join(" + ") || "Nenhum canal ativado";
}

function renderState(state) {
  elements.currentMonitorDetail.hidden = false;
  elements.statusIcon.textContent = statusIcon(state.status);
  elements.statusMessage.textContent = state.message;
  elements.lastCheck.textContent = state.checked_at
    ? `Última verificação: ${new Date(state.checked_at).toLocaleString("pt-BR")}`
    : "A última verificação aparecerá aqui.";
  renderQuery(state.query);
}

function monitorAction(label, action, destructive = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `text-button${destructive ? " text-button-danger" : ""}`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

function renderMonitors(items) {
  currentMonitors = items;
  if (items.length > 0 && !items.some((item) => item.monitoring_id === selectedMonitorId)) {
    selectedMonitorId = items[0].monitoring_id;
  }
  elements.monitorsList.replaceChildren();
  elements.monitorsEmpty.hidden = items.length > 0;
  elements.statusBadge.textContent = `${items.length} ${items.length === 1 ? "MONITOR" : "MONITORES"}`;
  elements.statusBadge.className = `status-badge ${
    items.some((item) => item.running) ? "status-aguardando" : "status-parado"
  }`;

  items.forEach((state) => {
    const card = document.createElement("article");
    card.className = "monitor-card";
    if (state.monitoring_id === selectedMonitorId) card.classList.add("selected");

    const heading = document.createElement("div");
    heading.className = "monitor-card-heading";
    const identity = document.createElement("span");
    identity.className = "monitor-identity";
    identity.textContent = `Monitor #${state.monitoring_id}`;
    const badge = document.createElement("span");
    badge.className = `status-badge ${statusClass(state.status)}`;
    badge.textContent = state.status;
    heading.append(identity, badge);

    const route = document.createElement("strong");
    route.className = "monitor-route";
    route.textContent = `${stationName(state.query.origin)} → ${stationName(state.query.destination)}`;
    const meta = document.createElement("p");
    meta.className = "monitor-meta";
    meta.textContent = `${formatTravelDate(state.query.travel_date)} · ${state.query.travel_class} · 1 passageiro · ${state.query.check_interval_seconds}s`;
    const message = document.createElement("p");
    message.className = "monitor-message";
    message.textContent = state.message;

    const actions = document.createElement("div");
    actions.className = "monitor-actions";
    actions.append(
      monitorAction("Ver histórico", async () => {
        selectedMonitorId = state.monitoring_id;
        renderMonitors(currentMonitors);
        renderState(state);
        await loadHistory(state.monitoring_id);
      }),
    );
    if (state.running) {
      actions.append(monitorAction("Pausar", () => pauseMonitor(state.monitoring_id)));
    } else {
      actions.append(monitorAction("Retomar", () => resumeMonitor(state.monitoring_id)));
    }
    actions.append(
      monitorAction("Remover", () => removeMonitor(state.monitoring_id), true),
    );
    card.append(heading, route, meta, message, actions);
    elements.monitorsList.append(card);
  });

  if (items.length === 0) {
    selectedMonitorId = null;
    elements.currentMonitorDetail.hidden = true;
    renderHistory([]);
    return;
  }
  const selected = items.find((item) => item.monitoring_id === selectedMonitorId) || items[0];
  selectedMonitorId = selected.monitoring_id;
  renderState(selected);
}

function renderHistory(items) {
  elements.historyList.replaceChildren();
  elements.historyEmpty.hidden = items.length > 0;
  items.forEach((entry) => {
    const item = document.createElement("li");
    item.className = "history-item";

    const result = document.createElement("span");
    result.className = `history-result history-result-${entry.result.toLowerCase().replaceAll("_", "-")}`;
    result.textContent = entry.result;

    const details = document.createElement("div");
    details.className = "history-details";
    const message = document.createElement("strong");
    message.textContent = entry.message;
    const checkedAt = document.createElement("time");
    checkedAt.dateTime = entry.checked_at;
    checkedAt.textContent = new Date(entry.checked_at).toLocaleString("pt-BR");
    details.append(message, checkedAt);
    item.append(result, details);
    elements.historyList.append(item);
  });
}

async function loadHistory(monitoringId = selectedMonitorId) {
  if (!monitoringId) {
    renderHistory([]);
    return;
  }
  try {
    const history = await fetchJson(
      `/api/monitoramentos/${monitoringId}/historico?limit=10`,
    );
    renderHistory(history.items);
  } catch (error) {
    elements.historyList.replaceChildren();
    elements.historyEmpty.hidden = false;
    elements.historyEmpty.textContent = `Histórico indisponível: ${error.message}`;
  }
}

function isIos() {
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
}

function urlBase64ToUint8Array(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const normalized = (value + padding).replaceAll("-", "+").replaceAll("_", "/");
  return Uint8Array.from(window.atob(normalized), (character) => character.charCodeAt(0));
}

function pushSubscriptionPayload(subscription) {
  const serialized = subscription.toJSON();
  return {
    device_id: pushDeviceId,
    endpoint: serialized.endpoint,
    keys: serialized.keys,
  };
}

function renderPushStatus(message, state = "idle") {
  elements.pushStatus.textContent = message;
  elements.pushStatus.dataset.state = state;
}

async function loadPushState() {
  elements.activatePush.disabled = true;
  try {
    pushConfig = await fetchJson("/api/push/config");
    if (!pushConfig.enabled || !pushConfig.configured) {
      renderPushStatus("INDISPONÍVEL — configure as chaves VAPID no servidor.", "unavailable");
      return;
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window) ||
        !("Notification" in window)) {
      renderPushStatus("NÃO SUPORTADO NESTE NAVEGADOR", "unavailable");
      return;
    }
    if (!window.isSecureContext) {
      renderPushStatus("HTTPS NECESSÁRIO FORA DO COMPUTADOR LOCAL", "unavailable");
      return;
    }
    if (isIos() && !isStandalone()) {
      elements.iosInstallHelp.hidden = false;
      renderPushStatus("INSTALAÇÃO NECESSÁRIA NO IPHONE", "attention");
      return;
    }

    pushRegistration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    const browserSubscription = await pushRegistration.pushManager.getSubscription();
    const serverStatus = await fetchJson(
      `/api/push/status?device_id=${encodeURIComponent(pushDeviceId)}`,
    );
    pushSubscribed = Boolean(browserSubscription && serverStatus.subscribed);

    if (Notification.permission === "denied") {
      renderPushStatus("BLOQUEADO NAS CONFIGURAÇÕES DO NAVEGADOR", "unavailable");
      return;
    }
    if (pushSubscribed) {
      renderPushStatus("ATIVO NESTE DISPOSITIVO", "active");
      elements.activatePush.hidden = true;
      elements.testPush.hidden = false;
      elements.disablePush.hidden = false;
    } else {
      renderPushStatus("PRONTO PARA ATIVAR", "ready");
      elements.activatePush.hidden = false;
      elements.activatePush.disabled = false;
      elements.testPush.hidden = true;
      elements.disablePush.hidden = true;
    }
    renderQuery(currentQuery);
  } catch (error) {
    renderPushStatus(`INDISPONÍVEL — ${error.message}`, "unavailable");
  }
}

async function activatePush() {
  renderPushStatus("AGUARDANDO SUA AUTORIZAÇÃO…", "attention");
  elements.activatePush.disabled = true;
  try {
    if (!pushRegistration || !pushConfig?.public_key) {
      throw new Error("Web Push ainda não está pronto neste dispositivo.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      throw new Error("A permissão de notificações não foi concedida.");
    }
    let subscription = await pushRegistration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await pushRegistration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(pushConfig.public_key),
      });
    }
    await fetchJson("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(pushSubscriptionPayload(subscription)),
    });
    await loadPushState();
  } catch (error) {
    renderPushStatus(error.message, "unavailable");
    elements.activatePush.disabled = false;
  }
}

async function disablePush() {
  try {
    const subscription = await pushRegistration?.pushManager.getSubscription();
    if (subscription) {
      await fetchJson("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ device_id: pushDeviceId, endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
    }
    pushSubscribed = false;
    await loadPushState();
  } catch (error) {
    renderPushStatus(`Não foi possível desativar: ${error.message}`, "unavailable");
  }
}

async function testPush() {
  elements.testPush.disabled = true;
  renderPushStatus("ENVIANDO TESTE…", "attention");
  try {
    await fetchJson("/api/push/test", {
      method: "POST",
      body: JSON.stringify({ device_id: pushDeviceId }),
    });
    renderPushStatus("TESTE ENVIADO PARA ESTE DISPOSITIVO", "active");
  } catch (error) {
    renderPushStatus(`TESTE NÃO ENVIADO — ${error.message}`, "unavailable");
  } finally {
    elements.testPush.disabled = false;
  }
}

async function startMonitoring(event) {
  event.preventDefault();
  setFormError();
  if (elements.origin.value === elements.destination.value) {
    setFormError("Origem e destino devem ser diferentes.");
    return;
  }

  try {
    elements.startButton.disabled = true;
    const state = await fetchJson("/api/monitoramentos", {
      method: "POST",
      body: JSON.stringify({
        origin: elements.origin.value,
        destination: elements.destination.value,
        travel_date: elements.travelDate.value,
        travel_class: elements.travelClass.options[elements.travelClass.selectedIndex].text,
        passengers: 1,
        interval_seconds: Number(elements.interval.value),
        whatsapp_enabled: elements.whatsappAlerts.checked,
        sms_enabled: elements.smsAlerts.checked,
        push_device_id: pushSubscribed ? pushDeviceId : null,
      }),
    });
    selectedMonitorId = state.monitoring_id;
    await refreshState();
    setControls(false);
  } catch (error) {
    setFormError(error.message);
    setControls(false);
  }
}

async function stopMonitoring() {
  setFormError();
  try {
    elements.stopButton.disabled = true;
    renderState(await fetchJson("/api/monitoramento", { method: "DELETE" }));
  } catch (error) {
    setFormError(error.message);
  }
}

async function pauseMonitor(monitoringId) {
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}/pausar`, { method: "POST" });
    await refreshState();
  } catch (error) {
    setFormError(error.message);
  }
}

async function resumeMonitor(monitoringId) {
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}/retomar`, { method: "POST" });
    selectedMonitorId = monitoringId;
    await refreshState();
  } catch (error) {
    setFormError(error.message);
  }
}

async function removeMonitor(monitoringId) {
  const confirmed = window.confirm(
    `Remover o monitor #${monitoringId}? O histórico continuará preservado localmente.`,
  );
  if (!confirmed) return;
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}`, { method: "DELETE" });
    if (selectedMonitorId === monitoringId) selectedMonitorId = null;
    await refreshState();
  } catch (error) {
    setFormError(error.message);
  }
}

async function refreshState() {
  try {
    const response = await fetchJson("/api/monitoramentos");
    renderMonitors(response.items);
    await loadHistory(selectedMonitorId);
  } catch (error) {
    setFormError(`Não foi possível atualizar o estado: ${error.message}`);
  }
}

elements.form.addEventListener("submit", startMonitoring);
elements.stopButton.addEventListener("click", stopMonitoring);
elements.refreshHistory.addEventListener("click", () => loadHistory(selectedMonitorId));
elements.activatePush.addEventListener("click", activatePush);
elements.testPush.addEventListener("click", testPush);
elements.disablePush.addEventListener("click", disablePush);
elements.installApp.addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  elements.installApp.hidden = true;
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  elements.installApp.hidden = false;
});

window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  elements.installApp.hidden = true;
});

Promise.all([loadCatalog(), refreshState(), loadPushState()]).finally(() => {
  window.setInterval(refreshState, 3000);
});
