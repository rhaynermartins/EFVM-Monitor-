"use strict";

const form = document.querySelector("#auth-form");
const submitButton = document.querySelector("#auth-submit");
const errorMessage = document.querySelector("#auth-error");
const mode = document.body.dataset.authMode;

function responseMessage(body) {
  if (typeof body?.detail === "string") return body.detail;
  if (Array.isArray(body?.detail)) return body.detail.map((item) => item.msg).join(" ");
  return "Não foi possível concluir o acesso.";
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorMessage.hidden = true;
  submitButton.disabled = true;
  submitButton.textContent = mode === "register" ? "Criando conta…" : "Entrando…";

  const payload = {
    email: document.querySelector("#auth-email").value.trim(),
    password: document.querySelector("#auth-password").value,
  };
  if (mode === "register") {
    payload.name = document.querySelector("#auth-name").value.trim();
  }

  try {
    const endpoint = mode === "register" ? "/api/auth/cadastro" : "/api/auth/login";
    const response = await fetch(endpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(responseMessage(body));
    window.location.assign("/");
  } catch (error) {
    errorMessage.textContent = error.message;
    errorMessage.hidden = false;
    submitButton.disabled = false;
    submitButton.textContent = mode === "register" ? "Criar conta" : "Entrar";
  }
});
