document.addEventListener("DOMContentLoaded", () => {
  const darkModeToggle = document.getElementById("themeToggle");
  const body = document.body;

  const savedTheme = localStorage.getItem("theme");
  const systemPrefersDark = window.matchMedia(
    "(prefers-color-scheme: dark)"
  ).matches;

  if (savedTheme === "dark" || (!savedTheme && systemPrefersDark)) {
    body.classList.add("dark");
  }

  const updateToggleButton = () => {
    const isDark = body.classList.contains("dark");
    darkModeToggle.innerHTML = isDark
      ? `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="icon">
           <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
         </svg>`
      : `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="icon">
           <path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
         </svg>`;
  };

  updateToggleButton();

  darkModeToggle.addEventListener("click", () => {
    body.classList.toggle("dark");
    const isDark = body.classList.contains("dark");
    localStorage.setItem("theme", isDark ? "dark" : "light");
    updateToggleButton();
  });

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (e) => {
      if (!localStorage.getItem("theme")) {
        if (e.matches) {
          body.classList.add("dark");
        } else {
          body.classList.remove("dark");
        }
        updateToggleButton();
      }
    });
});

document.addEventListener("DOMContentLoaded", () => {
  const cookieConsentBanner = document.getElementById("cookieConsentBanner");
  const acceptCookieConsentButton = document.getElementById(
    "acceptCookieConsent"
  );

  if (cookieConsentBanner && acceptCookieConsentButton) {
    if (localStorage.getItem("cookieConsent") !== "accepted") {
      setTimeout(() => {
        cookieConsentBanner.classList.remove("hidden");
      }, 500);
    }

    acceptCookieConsentButton.addEventListener("click", () => {
      localStorage.setItem("cookieConsent", "accepted");
      cookieConsentBanner.classList.add("hidden");
    });
  }
});

const resetPasswordForm = document.getElementById("reset-password-form");
const messageArea = document.getElementById("message-area");
const tokenErrorArea = document.getElementById("token-error");
const tokenInput = document.getElementById("reset_token");
const signinLinkArea = document.getElementById("signin-link-area");
const resetPasswordButton = document.getElementById("reset-password-button");
const resetPasswordText = document.getElementById("reset-password-text");
const resetPasswordSpinner = document.getElementById("reset-password-spinner");

document.addEventListener("DOMContentLoaded", () => {
  const urlParams = new URLSearchParams(window.location.search);
  const token = urlParams.get("token");
  if (token) {
    tokenInput.value = token;
  } else {
    tokenErrorArea.style.display = "block";
    resetPasswordForm.style.display = "none";
  }
});

resetPasswordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  messageArea.style.display = "none";
  messageArea.className = "message-area";
  signinLinkArea.style.display = "none";

  resetPasswordButton.disabled = true;
  resetPasswordText.style.display = "none";
  resetPasswordSpinner.style.display = "inline-block";

  const formData = new FormData(resetPasswordForm);
  const newPassword = formData.get("new_password");
  const confirmPassword = formData.get("confirm_password");
  const token = formData.get("token");

  if (newPassword !== confirmPassword) {
    messageArea.textContent = "Passwords do not match.";
    messageArea.classList.add("message-error");
    messageArea.style.display = "block";
    resetPasswordButton.disabled = false;
    resetPasswordText.style.display = "inline";
    resetPasswordSpinner.style.display = "none";
    return;
  }

  if (newPassword.length < 8) {
    messageArea.textContent = "Password must be at least 8 characters long.";
    messageArea.classList.add("message-error");
    messageArea.style.display = "block";
    resetPasswordButton.disabled = false;
    resetPasswordText.style.display = "inline";
    resetPasswordSpinner.style.display = "none";
    return;
  }

  if (!token) {
    messageArea.textContent =
      "Reset token is missing. Please try the reset link again.";
    messageArea.classList.add("message-error");
    messageArea.style.display = "block";
    tokenErrorArea.style.display = "block";
    resetPasswordForm.style.display = "none";
    resetPasswordButton.disabled = false;
    resetPasswordText.style.display = "inline";
    resetPasswordSpinner.style.display = "none";
    return;
  }

  try {
    const response = await fetch("/auth/reset-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({
        token: token,
        new_password: newPassword,
      }),
    });

    const result = await response.json();

    if (response.ok) {
      resetPasswordForm.style.display = "none";
      document.querySelector("h2").style.display = "none";

      messageArea.textContent =
        "Your password has been reset. Please try signing in.";
      messageArea.classList.remove("message-error");
      messageArea.classList.add("message-box", "message-success");
      messageArea.style.display = "block";

      signinLinkArea.innerHTML = `
        <div class="text-center mt-4">
          <a href="/signin"
            class="inline-block px-6 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors duration-200">
            Sign In
          </a>
        </div>`;
      signinLinkArea.style.display = "block";
    } else {
      messageArea.textContent =
        result.detail ||
        "Failed to reset password. The link may be invalid or expired.";
      messageArea.classList.remove("message-success");
      messageArea.classList.add("message-box", "message-error");
      messageArea.style.display = "block";
      signinLinkArea.style.display = "none";
    }
  } catch (error) {
    console.error("Reset password error:", error);
    messageArea.textContent = "An unexpected error occurred. Please try again.";
    messageArea.classList.remove("message-success");
    messageArea.classList.add("message-box", "message-error");
    messageArea.style.display = "block";
    signinLinkArea.style.display = "none";
  } finally {
    resetPasswordButton.disabled = false;
    resetPasswordText.style.display = "inline";
    resetPasswordSpinner.style.display = "none";
  }
});
