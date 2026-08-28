"use strict";

const elements = {
  form: document.querySelector("#monitor-form"),
  origin: document.querySelector("#origin"),
  destination: document.querySelector("#destination"),
  travelDate: document.querySelector("#travel-date"),
  travelClass: document.querySelector("#travel-class"),
  passengers: document.querySelector("#passengers"),
  interval: document.querySelector("#interval"),
  notifications: document.querySelector("#browser-notifications"),
  startButton: document.querySelector("#start-button"),
  stopButton: document.querySelector("#stop-button"),
  catalogState: document.querySelector("#catalog-state"),
  formError: document.querySelector("#form-error"),
  statusBadge: document.querySelector("#status-badge"),
  statusIcon: document.querySelector("#status-icon"),
  statusMessage: document.querySelector("#status-message"),
  lastCheck: document.querySelector("#last-check"),
  querySummary: document.querySelector("#query-summary"),
  summaryRoute: document.querySelector("#summary-route"),
  summaryDateClass: document.querySelector("#summary-date-class"),
  summaryInterval: document.querySelector("#summary-interval"),
  refreshHistory: document.querySelector("#refresh-history"),
  historyEmpty: document.querySelector("#history-empty"),
  historyList: document.querySelector("#history-list"),
};

let catalogReady = false;
let stationsById = new Map();
let previousStatus = null;

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

function setControls(running) {
  const formControls = [
    elements.origin,
    elements.destination,
    elements.travelDate,
    elements.travelClass,
    elements.interval,
    elements.notifications,
  ];
  formControls.forEach((control) => {
    control.disabled = running || !catalogReady;
  });
  elements.passengers.disabled = true;
  elements.startButton.disabled = running || !catalogReady;
  elements.stopButton.disabled = !running;
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

function renderQuery(query) {
  elements.querySummary.hidden = !query;
  if (!query) return;
  elements.summaryRoute.textContent = `${stationName(query.origin)} → ${stationName(query.destination)}`;
  elements.summaryDateClass.textContent = `${formatTravelDate(query.travel_date)} · ${query.travel_class} · 1 passageiro`;
  elements.summaryInterval.textContent = `${query.check_interval_seconds} segundos`;
}

function maybeNotify(state) {
  const becameAvailable = state.status === "TEM_VAGA" && previousStatus !== "TEM_VAGA";
  const notificationsEnabled = elements.notifications.checked && "Notification" in window;
  if (becameAvailable && notificationsEnabled && Notification.permission === "granted") {
    new Notification("Passagem encontrada na EFVM", {
      body: state.message,
      tag: "efvm-availability",
    });
  }
}

function renderState(state) {
  maybeNotify(state);
  elements.statusBadge.textContent = state.status;
  elements.statusBadge.className = `status-badge ${statusClass(state.status)}`;
  elements.statusIcon.textContent = statusIcon(state.status);
  elements.statusMessage.textContent = state.message;
  elements.lastCheck.textContent = state.checked_at
    ? `Última verificação: ${new Date(state.checked_at).toLocaleString("pt-BR")}`
    : "A última verificação aparecerá aqui.";
  renderQuery(state.query);
  setControls(state.running);
  if (state.status === "ENCERRANDO") elements.stopButton.disabled = true;
  previousStatus = state.status;
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

async function loadHistory() {
  try {
    const history = await fetchJson("/api/monitoramento/historico?limit=10");
    renderHistory(history.items);
  } catch (error) {
    elements.historyList.replaceChildren();
    elements.historyEmpty.hidden = false;
    elements.historyEmpty.textContent = `Histórico indisponível: ${error.message}`;
  }
}

async function requestNotificationPermission() {
  if (!elements.notifications.checked || !("Notification" in window)) return;
  if (Notification.permission === "default") await Notification.requestPermission();
}

async function startMonitoring(event) {
  event.preventDefault();
  setFormError();
  if (elements.origin.value === elements.destination.value) {
    setFormError("Origem e destino devem ser diferentes.");
    return;
  }

  try {
    await requestNotificationPermission();
    elements.startButton.disabled = true;
    const state = await fetchJson("/api/monitoramento", {
      method: "POST",
      body: JSON.stringify({
        origin: elements.origin.value,
        destination: elements.destination.value,
        travel_date: elements.travelDate.value,
        travel_class: elements.travelClass.options[elements.travelClass.selectedIndex].text,
        passengers: 1,
        interval_seconds: Number(elements.interval.value),
      }),
    });
    renderState(state);
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

async function refreshState() {
  try {
    renderState(await fetchJson("/api/monitoramento"));
    await loadHistory();
  } catch (error) {
    setFormError(`Não foi possível atualizar o estado: ${error.message}`);
  }
}

elements.form.addEventListener("submit", startMonitoring);
elements.stopButton.addEventListener("click", stopMonitoring);
elements.refreshHistory.addEventListener("click", loadHistory);

Promise.all([loadCatalog(), refreshState()]).finally(() => {
  window.setInterval(refreshState, 3000);
});
