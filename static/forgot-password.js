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

const forgotPasswordForm = document.getElementById("forgot-password-form");
const messageArea = document.getElementById("message-area");
const sendResetLinkBtn = document.getElementById("send-reset-link-btn");
const sendResetLinkText = document.getElementById("send-reset-link-text");
const sendResetLinkSpinner = document.getElementById("send-reset-link-spinner");

forgotPasswordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  messageArea.style.display = "none";
  messageArea.className = "message-area";

  sendResetLinkBtn.disabled = true;
  sendResetLinkText.style.display = "none";
  sendResetLinkSpinner.style.display = "inline-block";

  const formData = new FormData(forgotPasswordForm);
  const email = formData.get("email");

  try {
    const response = await fetch("/auth/forgot-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ email: email }),
    });

    const result = await response.json();

    if (response.ok) {
      messageArea.textContent =
        result.detail ||
        "If your email is registered, you will receive a password reset link shortly.";
      messageArea.classList.add("message-success");
      forgotPasswordForm.reset();
      document.querySelector(".form-fields").style.display = "none";
      sendResetLinkBtn.style.display = "none";
      document.querySelector(".back-link").style.display = "none";
    } else {
      messageArea.textContent =
        result.detail || "Failed to send reset link. Please try again.";
      messageArea.classList.add("message-error");
    }
  } catch (error) {
    console.error("Forgot password error:", error);
    messageArea.textContent = "An unexpected error occurred. Please try again.";
    messageArea.classList.add("message-error");
  }

  sendResetLinkBtn.disabled = false;
  sendResetLinkText.style.display = "inline";
  sendResetLinkSpinner.style.display = "none";
  messageArea.style.display = "block";
});
