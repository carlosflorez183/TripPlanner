/* =========================================================
   TRIPLANNER — Autenticación con Amazon Cognito
   Usa el flujo USER_PASSWORD_AUTH directo (sin SDK de Cognito,
   solo llamadas al endpoint de Cognito Identity Provider).
   ========================================================= */

const Auth = (() => {
  // CONFIG se define en app.js antes de cargar este archivo.
  // Ojo: `const CONFIG` en app.js NO crea window.CONFIG, así que probamos ambos.
  const getConfig = () => {
    if (typeof CONFIG !== "undefined" && CONFIG) return CONFIG;
    return window.CONFIG || {};
  };

  const STORAGE_KEYS = {
    idToken: "tp_id_token",
    accessToken: "tp_access_token",
    refreshToken: "tp_refresh_token",
    userId: "tp_userId",
    userName: "tp_userName",
    userEmail: "tp_userEmail",
  };

  // ---------------------------------------------------------------------------
  // Token management
  // ---------------------------------------------------------------------------
  function saveTokens(idToken, accessToken, refreshToken, email, name) {
    localStorage.setItem(STORAGE_KEYS.idToken, idToken);
    localStorage.setItem(STORAGE_KEYS.accessToken, accessToken);
    if (refreshToken) localStorage.setItem(STORAGE_KEYS.refreshToken, refreshToken);
    if (email) {
      localStorage.setItem(STORAGE_KEYS.userId, email);
      localStorage.setItem(STORAGE_KEYS.userEmail, email);
    }
    if (name) localStorage.setItem(STORAGE_KEYS.userName, name);
  }

  function getIdToken() {
    return localStorage.getItem(STORAGE_KEYS.idToken);
  }

  function getAccessToken() {
    return localStorage.getItem(STORAGE_KEYS.accessToken);
  }

  function getCurrentUser() {
    return {
      userId: localStorage.getItem(STORAGE_KEYS.userId) || "anonymous",
      email: localStorage.getItem(STORAGE_KEYS.userEmail) || "",
      name: localStorage.getItem(STORAGE_KEYS.userName) || "Viajero",
      idToken: getIdToken(),
      accessToken: getAccessToken(),
    };
  }

  function isLoggedIn() {
    const token = getIdToken();
    if (!token) return false;
    // Verificar expiración del JWT (decode básico del payload)
    try {
      const payload = JSON.parse(atob(token.split(".")[1]));
      return payload.exp * 1000 > Date.now();
    } catch {
      return false;
    }
  }

  function logout() {
    Object.values(STORAGE_KEYS).forEach(k => localStorage.removeItem(k));
    showAuthScreen();
  }

  // Refresca el IdToken si está por expirar (los tokens duran ~1h).
  // Necesario porque la API ahora exige un JWT válido en cada request.
  async function ensureFreshToken() {
    const token = getIdToken();
    if (!token) return;

    let expMs = 0;
    try {
      expMs = JSON.parse(atob(token.split(".")[1])).exp * 1000;
    } catch {
      return;
    }
    // Margen de 5 minutos
    if (expMs - Date.now() > 5 * 60 * 1000) return;

    const refreshToken = localStorage.getItem(STORAGE_KEYS.refreshToken);
    if (!refreshToken) return;

    const cfg = getConfig();
    if (!cfg.COGNITO_CLIENT_ID) return;

    const data = await cognitoRequest("InitiateAuth", {
      AuthFlow: "REFRESH_TOKEN_AUTH",
      ClientId: cfg.COGNITO_CLIENT_ID,
      AuthParameters: { REFRESH_TOKEN: refreshToken },
    });

    const result = data.AuthenticationResult || {};
    if (result.IdToken) localStorage.setItem(STORAGE_KEYS.idToken, result.IdToken);
    if (result.AccessToken) localStorage.setItem(STORAGE_KEYS.accessToken, result.AccessToken);
  }

  // ---------------------------------------------------------------------------
  // Cognito API calls (endpoint directo, sin SDK)
  // ---------------------------------------------------------------------------
  async function cognitoRequest(action, body) {
    const cfg = getConfig();
    // Validación temprana: sin ClientId Cognito responde
    // "Value null at 'clientId' failed to satisfy constraint: Member must not be null"
    if (!body.ClientId) {
      throw new Error(
        "Falta COGNITO_CLIENT_ID en la configuración del frontend (frontend/app.js). " +
        "Revisa que window.CONFIG esté definido antes de iniciar sesión."
      );
    }
    const response = await fetch(
      `https://cognito-idp.${cfg.COGNITO_REGION || "us-east-1"}.amazonaws.com/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/x-amz-json-1.1",
          "X-Amz-Target": `AWSCognitoIdentityProviderService.${action}`,
        },
        body: JSON.stringify(body),
      }
    );
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.message || data.__type || "Error de autenticación");
    }
    return data;
  }

  async function login(email, password) {
    const cfg = getConfig();
    const data = await cognitoRequest("InitiateAuth", {
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: cfg.COGNITO_CLIENT_ID,
      AuthParameters: {
        USERNAME: email,
        PASSWORD: password,
      },
    });

    const result = data.AuthenticationResult;
    // Decodificar nombre del ID token
    let name = email.split("@")[0];
    try {
      const payload = JSON.parse(atob(result.IdToken.split(".")[1]));
      name = payload.name || payload["cognito:username"] || name;
    } catch {}

    saveTokens(result.IdToken, result.AccessToken, result.RefreshToken, email, name);
    return { email, name };
  }

  async function register(email, password, name) {
    const cfg = getConfig();
    await cognitoRequest("SignUp", {
      ClientId: cfg.COGNITO_CLIENT_ID,
      Username: email,
      Password: password,
      UserAttributes: [
        { Name: "email", Value: email },
        { Name: "name", Value: name || email.split("@")[0] },
      ],
    });
    return true;
  }

  async function confirmSignUp(email, code) {
    const cfg = getConfig();
    await cognitoRequest("ConfirmSignUp", {
      ClientId: cfg.COGNITO_CLIENT_ID,
      Username: email,
      ConfirmationCode: code,
    });
    return true;
  }

  async function forgotPassword(email) {
    const cfg = getConfig();
    await cognitoRequest("ForgotPassword", {
      ClientId: cfg.COGNITO_CLIENT_ID,
      Username: email,
    });
    return true;
  }

  async function confirmForgotPassword(email, code, newPassword) {
    const cfg = getConfig();
    await cognitoRequest("ConfirmForgotPassword", {
      ClientId: cfg.COGNITO_CLIENT_ID,
      Username: email,
      ConfirmationCode: code,
      Password: newPassword,
    });
    return true;
  }

  // ---------------------------------------------------------------------------
  // UI — Pantalla de autenticación
  // ---------------------------------------------------------------------------
  function showAuthScreen() {
    document.getElementById("authScreen").style.display = "flex";
    document.getElementById("appShell").style.display = "none";
    showAuthTab("login");
  }

  function showApp(user) {
    document.getElementById("authScreen").style.display = "none";
    document.getElementById("appShell").style.display = "flex";
    // Actualizar UI con datos del usuario
    if (window.onUserLoggedIn) window.onUserLoggedIn(user);
  }

  function showAuthTab(tab) {
    document.querySelectorAll(".auth-tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".auth-panel").forEach(p => p.classList.remove("active"));
    document.getElementById(`tab-${tab}`)?.classList.add("active");
    document.getElementById(`panel-${tab}`)?.classList.add("active");
    clearAuthError();
  }

  function setAuthError(msg) {
    const el = document.getElementById("authError");
    if (el) { el.textContent = msg; el.style.display = "block"; }
  }

  function clearAuthError() {
    const el = document.getElementById("authError");
    if (el) { el.textContent = ""; el.style.display = "none"; }
  }

  function setAuthLoading(loading) {
    document.querySelectorAll(".auth-submit-btn").forEach(btn => {
      btn.disabled = loading;
      btn.textContent = loading ? "Cargando..." : btn.dataset.label;
    });
  }

  // ---------------------------------------------------------------------------
  // Event handlers (llamados desde HTML)
  // ---------------------------------------------------------------------------
  async function handleLogin(event) {
    event.preventDefault();
    clearAuthError();
    const email = document.getElementById("loginEmail").value.trim();
    const password = document.getElementById("loginPassword").value;
    if (!email || !password) { setAuthError("Completa todos los campos"); return; }

    setAuthLoading(true);
    try {
      const user = await login(email, password);
      showApp(user);
    } catch (e) {
      if (e.message.includes("not confirmed") || e.message.includes("UserNotConfirmedException")) {
        // Redirigir a confirmación
        document.getElementById("confirmEmail").value = email;
        showAuthTab("confirm");
        setAuthError("Tu cuenta necesita confirmación. Revisa tu email.");
      } else {
        setAuthError(e.message);
      }
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleRegister(event) {
    event.preventDefault();
    clearAuthError();
    const name = document.getElementById("registerName").value.trim();
    const email = document.getElementById("registerEmail").value.trim();
    const password = document.getElementById("registerPassword").value;
    const password2 = document.getElementById("registerPassword2").value;

    if (!name || !email || !password) { setAuthError("Completa todos los campos"); return; }
    if (password !== password2) { setAuthError("Las contraseñas no coinciden"); return; }
    if (password.length < 8) { setAuthError("La contraseña debe tener al menos 8 caracteres"); return; }

    setAuthLoading(true);
    try {
      await register(email, password, name);
      document.getElementById("confirmEmail").value = email;
      showAuthTab("confirm");
      setAuthError(""); // limpiar
      document.getElementById("confirmInfo").textContent =
        `Código enviado a ${email}. Revisa tu bandeja de entrada.`;
    } catch (e) {
      if (e.message.includes("UsernameExistsException") || e.message.includes("already exists")) {
        setAuthError("Este email ya está registrado. Inicia sesión.");
        showAuthTab("login");
      } else {
        setAuthError(e.message);
      }
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleConfirm(event) {
    event.preventDefault();
    clearAuthError();
    const email = document.getElementById("confirmEmail").value.trim();
    const code = document.getElementById("confirmCode").value.trim();
    if (!email || !code) { setAuthError("Completa todos los campos"); return; }

    setAuthLoading(true);
    try {
      await confirmSignUp(email, code);
      showAuthTab("login");
      document.getElementById("loginEmail").value = email;
      setAuthError("");
      document.getElementById("authSuccess").textContent = "✅ Cuenta confirmada. Ya puedes iniciar sesión.";
      document.getElementById("authSuccess").style.display = "block";
    } catch (e) {
      setAuthError(e.message);
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleForgotPassword(event) {
    event.preventDefault();
    clearAuthError();
    const email = document.getElementById("forgotEmail").value.trim();
    if (!email) { setAuthError("Ingresa tu email"); return; }

    setAuthLoading(true);
    try {
      await forgotPassword(email);
      document.getElementById("resetEmail").value = email;
      showAuthTab("reset");
      document.getElementById("authSuccess").textContent = `📧 Código enviado a ${email}`;
      document.getElementById("authSuccess").style.display = "block";
    } catch (e) {
      setAuthError(e.message);
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleResetPassword(event) {
    event.preventDefault();
    clearAuthError();
    const email = document.getElementById("resetEmail").value.trim();
    const code = document.getElementById("resetCode").value.trim();
    const newPwd = document.getElementById("resetPassword").value;
    if (!email || !code || !newPwd) { setAuthError("Completa todos los campos"); return; }

    setAuthLoading(true);
    try {
      await confirmForgotPassword(email, code, newPwd);
      showAuthTab("login");
      document.getElementById("loginEmail").value = email;
      document.getElementById("authSuccess").textContent = "✅ Contraseña cambiada. Ya puedes iniciar sesión.";
      document.getElementById("authSuccess").style.display = "block";
    } catch (e) {
      setAuthError(e.message);
    } finally {
      setAuthLoading(false);
    }
  }

  // ---------------------------------------------------------------------------
  // Inicialización
  // ---------------------------------------------------------------------------
  function init() {
    if (isLoggedIn()) {
      showApp(getCurrentUser());
    } else {
      showAuthScreen();
    }
  }

  return {
    init,
    login,
    logout,
    register,
    confirmSignUp,
    isLoggedIn,
    getCurrentUser,
    getIdToken,
    getAccessToken,
    ensureFreshToken,
    showAuthTab,
    handleLogin,
    handleRegister,
    handleConfirm,
    handleForgotPassword,
    handleResetPassword,
  };
})();

// Exponer también en window (app.js usaba window.Auth, que no existe con `const`).
window.Auth = Auth;
