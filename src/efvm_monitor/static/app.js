"use strict";

const elements = {
  browserIntro: document.querySelector("#browser-intro"),
  pwaIntro: document.querySelector("#pwa-intro"),
  connectionBanner: document.querySelector("#connection-banner"),
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
  androidInstallHelp: document.querySelector("#android-install-help"),
  desktopInstallHelp: document.querySelector("#desktop-install-help"),
  installTitle: document.querySelector("#install-title"),
  installStatus: document.querySelector("#install-status"),
  onboarding: document.querySelector("#onboarding"),
  onboardingProgress: document.querySelector("#onboarding-progress"),
  onboardingMonitor: document.querySelector("#onboarding-monitor"),
  onboardingInstall: document.querySelector("#onboarding-install"),
  onboardingPush: document.querySelector("#onboarding-push"),
  onboardingPushStatus: document.querySelector("#onboarding-push-status"),
  deviceSummary: document.querySelector("#device-summary"),
  deviceSummaryTitle: document.querySelector("#device-summary-title"),
  deviceSummaryDescription: document.querySelector("#device-summary-description"),
  startButton: document.querySelector("#start-button"),
  stopButton: document.querySelector("#stop-button"),
  catalogState: document.querySelector("#catalog-state"),
  formError: document.querySelector("#form-error"),
  statusBadge: document.querySelector("#status-badge"),
  statusIcon: document.querySelector("#status-icon"),
  statusMessage: document.querySelector("#status-message"),
  lastCheck: document.querySelector("#last-check"),
  nextCheck: document.querySelector("#next-check"),
  dashboardState: document.querySelector("#dashboard-state"),
  monitorsEmpty: document.querySelector("#monitors-empty"),
  monitorsList: document.querySelector("#monitors-list"),
  currentMonitorDetail: document.querySelector("#current-monitor-detail"),
  querySummary: document.querySelector("#query-summary"),
  summaryRoute: document.querySelector("#summary-route"),
  summaryDateClass: document.querySelector("#summary-date-class"),
  summaryInterval: document.querySelector("#summary-interval"),
  summaryAlerts: document.querySelector("#summary-alerts"),
  refreshHistory: document.querySelector("#refresh-history"),
  historySection: document.querySelector("#monitor-history"),
  historyEmpty: document.querySelector("#history-empty"),
  historyList: document.querySelector("#history-list"),
  logoutButton: document.querySelector("#logout-button"),
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
let appInstalled = isStandalone();
let pushLifecycleState = "checking";
let refreshInProgress = false;
const pushDeviceId = getPushDeviceId();
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const defaultMonitorsEmptyText = elements.monitorsEmpty.textContent;

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
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(url, {
    ...options,
    method,
    credentials: "same-origin",
    headers,
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Sua sessão expirou. Entre novamente.");
  }
  const body = await response.json();
  if (!response.ok) throw new Error(apiErrorMessage(body, "A solicitação não foi concluída."));
  return body;
}

async function logout() {
  elements.logoutButton.disabled = true;
  try {
    const browserSubscription = pushRegistration
      ? await pushRegistration.pushManager.getSubscription()
      : null;
    if (browserSubscription) {
      try {
        try {
          await fetchJson("/api/push/unsubscribe", {
            method: "POST",
            body: JSON.stringify({
              device_id: pushDeviceId,
              endpoint: browserSubscription.endpoint,
            }),
          });
        } finally {
          await browserSubscription.unsubscribe();
        }
      } catch (_error) {
        // O logout da conta deve continuar mesmo se o provedor de Push estiver indisponível.
      }
    }
    await fetchJson("/api/auth/logout", { method: "POST" });
    window.location.assign("/login");
  } catch (error) {
    setFormError(error.message);
    elements.logoutButton.disabled = false;
  }
}

function setFormError(message = "") {
  elements.formError.textContent = message;
  elements.formError.hidden = !message;
}

function userFacingError(error, fallback) {
  if (!navigator.onLine) return "Você está sem internet. Tente novamente quando a conexão voltar.";
  if (error instanceof TypeError) return fallback;
  return (error.message || fallback).replace(/\b(monitoramento)\s+#?\d+\b/gi, "$1");
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
    console.error("Não foi possível carregar o catálogo.", error);
    elements.catalogState.textContent = "Opções indisponíveis";
    elements.catalogState.className = "catalog-state error";
    setFormError("Não foi possível carregar as estações agora. Atualize a página para tentar novamente.");
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

function statusLabel(state) {
  if (!state.running) return "Pausado";
  return {
    AGUARDANDO: "Iniciando",
    ENCERRANDO: "Pausando",
    TEM_VAGA: "Passagem encontrada",
    SEM_VAGA: "Sem vagas",
    ERRO: "Atenção",
  }[state.status] || "Acompanhando";
}

function friendlyStatusMessage(state) {
  if (!state.running) return "Este monitor está pausado e não fará novas consultas.";
  return {
    AGUARDANDO: "A primeira consulta será feita em instantes.",
    ENCERRANDO: "O monitor está sendo pausado.",
    TEM_VAGA: "Passagem encontrada. Abra o portal oficial para concluir a compra.",
    SEM_VAGA: "Nenhuma vaga foi encontrada na última consulta.",
    ERRO: "Não foi possível verificar agora. O monitor tentará novamente.",
  }[state.status] || "O monitor está acompanhando esta viagem.";
}

function stationName(id) {
  return stationsById.get(String(id)) || String(id);
}

function formatTravelDate(value) {
  if (!value) return "—";
  const [year, month, day] = value.split("-");
  return `${day}/${month}/${year}`;
}

function formatInterval(seconds) {
  if (seconds < 60) return `${seconds} segundos`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} ${minutes === 1 ? "minuto" : "minutos"}`;
}

function formatDateTime(value) {
  return new Date(value).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function nextCheckMessage(state) {
  if (!state.running) return "Próxima verificação: monitor pausado.";
  if (!state.checked_at) return "Próxima verificação: assim que a primeira consulta começar.";
  const intervalSeconds = Number(state.query?.check_interval_seconds || 0);
  const nextCheckAt = new Date(state.checked_at).getTime() + intervalSeconds * 1000;
  if (!Number.isFinite(nextCheckAt) || nextCheckAt <= Date.now()) {
    return "Próxima verificação prevista: a qualquer momento.";
  }
  return `Próxima verificação prevista: ${new Date(nextCheckAt).toLocaleTimeString("pt-BR", {
    hour: "2-digit",
    minute: "2-digit",
  })}.`;
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
  const passengers = Number(query.passengers || 1);
  elements.summaryDateClass.textContent = `${formatTravelDate(query.travel_date)} · ${query.travel_class} · ${passengers} ${passengers === 1 ? "passageiro" : "passageiros"}`;
  elements.summaryInterval.textContent = formatInterval(query.check_interval_seconds);
  const alertChannels = [];
  if (pushSubscribed) alertChannels.push("Web Push (principal)");
  if (query.sms_enabled) alertChannels.push("SMS");
  if (query.whatsapp_enabled) alertChannels.push("WhatsApp");
  elements.summaryAlerts.textContent = alertChannels.join(" + ") || "Nenhum canal ativado";
}

function renderState(state) {
  elements.currentMonitorDetail.hidden = false;
  const visualStatus = state.running ? state.status : "PARADO";
  elements.currentMonitorDetail.dataset.state = visualStatus.toLowerCase().replaceAll("_", "-");
  elements.statusIcon.textContent = statusIcon(visualStatus);
  elements.statusMessage.textContent = friendlyStatusMessage(state);
  elements.lastCheck.textContent = state.checked_at
    ? `Última verificação: ${formatDateTime(state.checked_at)}.`
    : "A última verificação aparecerá aqui.";
  elements.nextCheck.textContent = nextCheckMessage(state);
  renderQuery(state.query);
}

function monitorAction(label, action, destructive = false, accessibleLabel = label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `text-button${destructive ? " text-button-danger" : ""}`;
  button.textContent = label;
  button.setAttribute("aria-label", accessibleLabel);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await action();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function renderMonitors(items) {
  currentMonitors = items;
  if (items.length > 0 && !items.some((item) => item.monitoring_id === selectedMonitorId)) {
    selectedMonitorId = items[0].monitoring_id;
  }
  elements.monitorsList.replaceChildren();
  elements.monitorsEmpty.hidden = items.length > 0;
  elements.dashboardState.hidden = true;
  elements.monitorsList.setAttribute("aria-busy", "false");
  elements.statusBadge.textContent = `${items.length} ${items.length === 1 ? "MONITOR" : "MONITORES"}`;
  elements.statusBadge.className = `status-badge ${
    items.some((item) => item.running) ? "status-aguardando" : "status-parado"
  }`;

  items.forEach((state) => {
    const routeLabel = `${stationName(state.query.origin)} para ${stationName(state.query.destination)}`;
    const visualStatus = state.running ? state.status : "PARADO";
    const card = document.createElement("article");
    card.className = "monitor-card";
    card.dataset.state = visualStatus.toLowerCase().replaceAll("_", "-");
    card.setAttribute("aria-label", `${routeLabel}: ${statusLabel(state)}`);
    if (state.monitoring_id === selectedMonitorId) card.classList.add("selected");

    const heading = document.createElement("div");
    heading.className = "monitor-card-heading";
    const identity = document.createElement("span");
    identity.className = "monitor-identity";
    identity.textContent = "MONITORAMENTO";
    const badge = document.createElement("span");
    badge.className = `status-badge ${statusClass(visualStatus)}`;
    badge.textContent = statusLabel(state);
    heading.append(identity, badge);

    const route = document.createElement("strong");
    route.className = "monitor-route";
    route.textContent = `${stationName(state.query.origin)} → ${stationName(state.query.destination)}`;
    const meta = document.createElement("p");
    meta.className = "monitor-meta";
    meta.textContent = `${formatTravelDate(state.query.travel_date)} · ${state.query.travel_class} · 1 passageiro · a cada ${formatInterval(state.query.check_interval_seconds)}`;
    const message = document.createElement("p");
    message.className = "monitor-message";
    message.textContent = friendlyStatusMessage(state);

    const actions = document.createElement("div");
    actions.className = "monitor-actions";
    actions.append(
      monitorAction(
        "Ver histórico",
        async () => {
          selectedMonitorId = state.monitoring_id;
          renderMonitors(currentMonitors);
          renderState(state);
          const historyDisplayed = await loadHistory(state.monitoring_id);
          if (historyDisplayed) {
            elements.historySection.scrollIntoView({ behavior: "smooth", block: "start" });
          }
        },
        false,
        `Ver histórico da viagem de ${routeLabel}`,
      ),
    );
    if (state.running) {
      actions.append(
        monitorAction(
          "Pausar",
          () => pauseMonitor(state.monitoring_id),
          false,
          `Pausar a viagem de ${routeLabel}`,
        ),
      );
    } else {
      actions.append(
        monitorAction(
          "Retomar",
          () => resumeMonitor(state.monitoring_id),
          false,
          `Retomar a viagem de ${routeLabel}`,
        ),
      );
    }
    actions.append(
      monitorAction(
        "Remover",
        () => removeMonitor(state.monitoring_id),
        true,
        `Remover a viagem de ${routeLabel}`,
      ),
    );
    card.append(heading, route, meta, message, actions);
    elements.monitorsList.append(card);
  });

  if (items.length === 0) {
    selectedMonitorId = null;
    elements.currentMonitorDetail.hidden = true;
    renderHistory([]);
    updateOnboarding();
    return;
  }
  const selected = items.find((item) => item.monitoring_id === selectedMonitorId) || items[0];
  selectedMonitorId = selected.monitoring_id;
  renderState(selected);
  updateOnboarding();
}

function renderHistory(items) {
  elements.historyList.replaceChildren();
  elements.historyEmpty.hidden = items.length > 0;
  items.forEach((entry) => {
    const item = document.createElement("li");
    item.className = "history-item";

    const result = document.createElement("span");
    result.className = `history-result history-result-${entry.result.toLowerCase().replaceAll("_", "-")}`;
    result.textContent = {
      TEM_VAGA: "Encontrada",
      SEM_VAGA: "Sem vagas",
      ERRO: "Atenção",
    }[entry.result] || "Consulta";

    const details = document.createElement("div");
    details.className = "history-details";
    const message = document.createElement("strong");
    message.textContent = {
      TEM_VAGA: "Passagem encontrada 🔔",
      SEM_VAGA: "Nenhuma vaga encontrada",
      ERRO: "Não foi possível verificar",
    }[entry.result] || "Consulta realizada";
    const checkedAt = document.createElement("time");
    checkedAt.dateTime = entry.checked_at;
    checkedAt.textContent = formatDateTime(entry.checked_at);
    details.append(message, checkedAt);
    item.append(result, details);
    elements.historyList.append(item);
  });
}

async function loadHistory(monitoringId = selectedMonitorId) {
  if (!monitoringId) {
    delete elements.historySection.dataset.monitoringId;
    renderHistory([]);
    return false;
  }
  elements.historySection.dataset.monitoringId = String(monitoringId);
  try {
    const history = await fetchJson(
      `/api/monitoramentos/${monitoringId}/historico?limit=10`,
    );
    if (monitoringId !== selectedMonitorId) return false;
    renderHistory(history.items);
    return true;
  } catch (error) {
    if (monitoringId !== selectedMonitorId) return false;
    console.error("Não foi possível carregar o histórico.", error);
    elements.historyList.replaceChildren();
    elements.historyEmpty.hidden = false;
    elements.historyEmpty.textContent = "Não foi possível carregar o histórico agora. Tente novamente.";
    return true;
  }
}

function isIos() {
  return /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
    (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);
}

function isAndroid() {
  return /android/i.test(window.navigator.userAgent);
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
}

function isMobileDevice() {
  return isIos() || isAndroid() || window.navigator.userAgentData?.mobile === true ||
    /mobile/i.test(window.navigator.userAgent);
}

function updateDisplayContext() {
  const mobile = isMobileDevice();
  // Installed elsewhere does not mean this browser window is running standalone.
  const mobilePwa = mobile && isStandalone();
  document.body.dataset.displayContext = mobile ? (mobilePwa ? "mobile-pwa" : "mobile-browser") : "desktop";
  if (elements.browserIntro) elements.browserIntro.hidden = mobilePwa;
  if (elements.pwaIntro) elements.pwaIntro.hidden = !mobilePwa;
  elements.onboarding.hidden = mobilePwa;
  elements.monitorsEmpty.textContent = mobile
    ? "Você ainda não acompanha nenhuma viagem. Configure a viagem acima para criar seu primeiro monitor."
    : defaultMonitorsEmptyText;
}

function notificationPermission() {
  return "Notification" in window ? Notification.permission : "unsupported";
}

function setOnboardingStep(step, complete, pendingMarker) {
  step.classList.toggle("is-complete", complete);
  step.querySelector(".onboarding-step-marker").textContent = complete ? "✓" : pendingMarker;
}

function updateDeviceSummary() {
  let state = "ready";
  let title = "Este dispositivo ainda não recebe alertas";
  let description = "Ative o Web Push para ser avisado quando uma passagem aparecer.";

  if (!navigator.onLine) {
    state = "attention";
    title = "Este dispositivo está sem conexão";
    description = "Seus monitores continuam no servidor e voltarão a aparecer quando a internet retornar.";
  } else if (pushSubscribed) {
    state = "protected";
    title = "Este dispositivo está protegido";
    description = "O Web Push está ativo e pode avisar mesmo com o aplicativo fechado.";
  } else if (notificationPermission() === "denied") {
    state = "blocked";
    title = "Notificações bloqueadas neste dispositivo";
    description = "Libere as notificações nas configurações do navegador ou do celular.";
  } else if (isIos() && !appInstalled) {
    state = "attention";
    title = "Instale o aplicativo no iPhone";
    description = "A instalação pela Tela de Início é necessária antes de ativar os alertas.";
  } else if (pushLifecycleState === "unavailable") {
    state = "blocked";
    title = "Alertas indisponíveis neste momento";
    description = "Tente novamente mais tarde ou use outro navegador compatível.";
  } else if (pushLifecycleState === "needs-renewal") {
    state = "attention";
    title = "Este dispositivo precisa ser protegido novamente";
    description = "Toque em “Ativar alertas” para renovar a proteção.";
  }

  elements.deviceSummary.dataset.state = state;
  elements.deviceSummaryTitle.textContent = title;
  elements.deviceSummaryDescription.textContent = description;
}

function updateOnboarding() {
  const hasMonitor = currentMonitors.length > 0;
  const installationComplete = appInstalled || isStandalone() || (!isIos() && !isAndroid());
  setOnboardingStep(elements.onboardingMonitor, hasMonitor, "2");
  setOnboardingStep(elements.onboardingInstall, installationComplete, "3");
  setOnboardingStep(elements.onboardingPush, pushSubscribed, "4");

  const completedSteps = 1 + Number(hasMonitor) + Number(installationComplete) + Number(pushSubscribed);
  elements.onboardingProgress.textContent = completedSteps === 4
    ? "Tudo pronto"
    : `${completedSteps} de 4 concluídos`;
  elements.onboarding.classList.toggle("is-complete", completedSteps === 4);
  elements.onboardingPushStatus.textContent = pushSubscribed
    ? "Alertas ativos neste dispositivo."
    : notificationPermission() === "denied"
      ? "As notificações estão bloqueadas nas configurações."
      : isMobileDevice()
        ? "Ative o Web Push no card da viagem para receber alertas."
        : "Ative o Web Push no Passo 1 para receber alertas.";
  updateDeviceSummary();
}

function configureInstallationExperience() {
  updateDisplayContext();
  appInstalled = appInstalled || isStandalone();
  const installedHere = isMobileDevice() ? isStandalone() : appInstalled;
  elements.iosInstallHelp.hidden = true;
  elements.androidInstallHelp.hidden = true;
  elements.desktopInstallHelp.hidden = true;
  elements.installApp.hidden = !deferredInstallPrompt || installedHere;

  if (installedHere) {
    elements.installTitle.textContent = "Aplicativo instalado";
    elements.installStatus.textContent = "O EFVM já está pronto neste dispositivo.";
  } else if (isIos()) {
    elements.installTitle.textContent = "Instale o EFVM no seu iPhone";
    elements.installStatus.textContent = "Siga os passos abaixo para receber alertas com o aplicativo fechado.";
    elements.iosInstallHelp.hidden = false;
  } else if (isAndroid()) {
    elements.installTitle.textContent = "Instale o EFVM no seu celular";
    elements.installStatus.textContent = "Use o Chrome e siga os passos abaixo.";
    elements.androidInstallHelp.hidden = false;
  } else {
    elements.installTitle.textContent = "Instale o EFVM";
    elements.installStatus.textContent = "A instalação é opcional neste computador.";
    elements.desktopInstallHelp.hidden = false;
  }
  updateOnboarding();
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
  elements.pushStatus.closest(".push-option").dataset.state = state;
  pushLifecycleState = state;
  updateOnboarding();
}

async function loadPushState() {
  elements.activatePush.disabled = true;
  try {
    pushConfig = await fetchJson("/api/push/config");
    if (!pushConfig.enabled || !pushConfig.configured) {
      renderPushStatus("Alertas indisponíveis no momento.", "unavailable");
      return;
    }
    if (!("serviceWorker" in navigator) || !("PushManager" in window) ||
        !("Notification" in window)) {
      renderPushStatus("Este navegador não oferece notificações.", "unavailable");
      return;
    }
    if (!window.isSecureContext) {
      renderPushStatus("Abra o endereço seguro do EFVM Monitor para ativar alertas.", "unavailable");
      return;
    }
    if (isIos() && !isStandalone()) {
      renderPushStatus("Instale o aplicativo no iPhone antes de ativar os alertas.", "attention");
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
      renderPushStatus("Notificações bloqueadas. Libere nas configurações do dispositivo.", "blocked");
      return;
    }
    if (pushSubscribed) {
      renderPushStatus("Alertas ativos neste dispositivo.", "active");
      elements.activatePush.hidden = true;
      elements.testPush.hidden = false;
      elements.disablePush.hidden = false;
    } else if (browserSubscription && !serverStatus.subscribed) {
      renderPushStatus("Este dispositivo precisa ser ativado novamente.", "needs-renewal");
      elements.activatePush.hidden = false;
      elements.activatePush.disabled = false;
      elements.testPush.hidden = true;
      elements.disablePush.hidden = true;
    } else {
      renderPushStatus("Pronto para ativar neste dispositivo.", "ready");
      elements.activatePush.hidden = false;
      elements.activatePush.disabled = false;
      elements.testPush.hidden = true;
      elements.disablePush.hidden = true;
    }
    renderQuery(currentQuery);
  } catch (error) {
    console.error("Não foi possível confirmar o Web Push.", error);
    renderPushStatus("Não foi possível confirmar os alertas agora. Tente novamente.", "unavailable");
  }
}

async function activatePush() {
  renderPushStatus("Aguardando sua autorização…", "attention");
  elements.activatePush.disabled = true;
  try {
    if (!pushRegistration || !pushConfig?.public_key) {
      throw new Error("Web Push ainda não está pronto neste dispositivo.");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      throw new Error("A notificação não foi autorizada. Você pode liberar nas configurações.");
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
    console.error("Não foi possível desativar o Web Push.", error);
    renderPushStatus("Não foi possível desativar agora. Tente novamente.", "unavailable");
  }
}

async function testPush() {
  elements.testPush.disabled = true;
  renderPushStatus("Enviando alerta de teste…", "attention");
  try {
    await fetchJson("/api/push/test", {
      method: "POST",
      body: JSON.stringify({ device_id: pushDeviceId }),
    });
    renderPushStatus("Alerta de teste enviado para este dispositivo.", "active");
  } catch (error) {
    console.error("Não foi possível enviar o alerta de teste.", error);
    renderPushStatus("O alerta de teste não foi enviado. Tente novamente.", "unavailable");
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
    elements.startButton.textContent = "Adicionando viagem…";
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
    document.querySelector(".status-panel")?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  } catch (error) {
    console.error("Não foi possível adicionar o monitoramento.", error);
    setFormError(userFacingError(error, "Não foi possível adicionar esta viagem agora. Tente novamente."));
    setControls(false);
  } finally {
    elements.startButton.textContent = "Adicionar monitoramento";
  }
}

async function stopMonitoring() {
  setFormError();
  try {
    elements.stopButton.disabled = true;
    renderState(await fetchJson("/api/monitoramento", { method: "DELETE" }));
  } catch (error) {
    console.error("Não foi possível pausar o monitoramento.", error);
    setFormError(userFacingError(error, "Não foi possível pausar esta viagem agora."));
  }
}

async function pauseMonitor(monitoringId) {
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}/pausar`, { method: "POST" });
    await refreshState();
  } catch (error) {
    console.error("Não foi possível pausar o monitoramento.", error);
    setFormError(userFacingError(error, "Não foi possível pausar esta viagem agora."));
  }
}

async function resumeMonitor(monitoringId) {
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}/retomar`, { method: "POST" });
    selectedMonitorId = monitoringId;
    await refreshState();
  } catch (error) {
    console.error("Não foi possível retomar o monitoramento.", error);
    setFormError(userFacingError(error, "Não foi possível retomar esta viagem agora."));
  }
}

async function removeMonitor(monitoringId) {
  const confirmed = window.confirm(
    "Remover este monitoramento? Ele deixará de aparecer na sua conta.",
  );
  if (!confirmed) return;
  setFormError();
  try {
    await fetchJson(`/api/monitoramentos/${monitoringId}`, { method: "DELETE" });
    if (selectedMonitorId === monitoringId) selectedMonitorId = null;
    await refreshState();
  } catch (error) {
    console.error("Não foi possível remover o monitoramento.", error);
    setFormError(userFacingError(error, "Não foi possível remover esta viagem agora."));
  }
}

async function refreshState() {
  if (refreshInProgress || !navigator.onLine) return;
  refreshInProgress = true;
  try {
    const response = await fetchJson("/api/monitoramentos");
    renderMonitors(response.items);
    await loadHistory(selectedMonitorId);
  } catch (error) {
    console.error("Não foi possível atualizar os monitoramentos.", error);
    elements.dashboardState.hidden = false;
    elements.dashboardState.dataset.state = "error";
    elements.dashboardState.textContent = "Não foi possível atualizar suas viagens agora. Tente novamente em instantes.";
    elements.monitorsList.setAttribute("aria-busy", "false");
  } finally {
    refreshInProgress = false;
  }
}

function updateConnectionState() {
  const offline = !navigator.onLine;
  elements.connectionBanner.hidden = !offline;
  document.body.classList.toggle("is-offline", offline);
  updateDeviceSummary();
  if (!offline) refreshState();
}

elements.form.addEventListener("submit", startMonitoring);
elements.stopButton.addEventListener("click", stopMonitoring);
elements.refreshHistory.addEventListener("click", () => loadHistory(selectedMonitorId));
elements.logoutButton?.addEventListener("click", logout);
elements.activatePush.addEventListener("click", activatePush);
elements.testPush.addEventListener("click", testPush);
elements.disablePush.addEventListener("click", disablePush);
elements.installApp.addEventListener("click", async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  configureInstallationExperience();
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  configureInstallationExperience();
});

window.addEventListener("appinstalled", () => {
  deferredInstallPrompt = null;
  appInstalled = true;
  configureInstallationExperience();
});

window.addEventListener("offline", updateConnectionState);
window.addEventListener("online", updateConnectionState);
window.addEventListener("pageshow", configureInstallationExperience);
window.matchMedia("(display-mode: standalone)").addEventListener("change", configureInstallationExperience);

configureInstallationExperience();
updateConnectionState();
Promise.all([loadCatalog(), refreshState(), loadPushState()]).finally(() => {
  window.setInterval(refreshState, 3000);
});
