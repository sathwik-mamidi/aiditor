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

const googleButton = document.getElementById("google-signin");
const signupButton = document.getElementById("signup-button");
const signupText = document.getElementById("signup-text");
const signupSpinner = document.getElementById("signup-spinner");
const signupForm = document.getElementById("signup-form");
const emailInput = document.getElementById("email");
const passwordInput = document.getElementById("password");
const errorDiv = document.getElementById("error-message");

signupForm.addEventListener("submit", async (e) => {
  e.preventDefault();

  const email = emailInput.value;
  const password = passwordInput.value;

  errorDiv.textContent = "";
  errorDiv.style.display = "none";

  if (!validateEmail(email)) {
    showError("Please enter a valid email address.");
    return;
  }

  if (!validatePassword(password)) {
    showError(
      "Password must be at least 8 characters long with uppercase, lowercase, number, and special character."
    );
    return;
  }

  signupButton.disabled = true;
  signupText.style.display = "none";
  signupSpinner.style.display = "inline-block";

  try {
    const response = await fetch("/signup", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ email, password }),
      redirect: "manual",
    });

    if (response.type === "opaqueredirect") {
      window.location.href = "/";
      return;
    }

    let data;
    try {
      if (response.headers.get("content-type")?.includes("application/json")) {
        data = await response.json();
      } else if (response.ok) {
        window.location.href = "/";
        return;
      }
    } catch (e) {
      if (response.ok) {
        console.warn(
          "Signup response was OK but JSON parsing failed. Redirecting.",
          e
        );
        window.location.href = "/";
        return;
      } else {
        console.error(
          "Signup error: Failed to parse JSON response for a non-OK status.",
          e,
          response
        );
        showError(
          "An unexpected error occurred. The server response was not valid."
        );
        return;
      }
    }

    if (!response.ok) {
      if (data && data.detail?.includes("Google Sign-In")) {
        errorDiv.innerHTML =
          data.detail +
          ' <a href="/auth/google" class="text-indigo-600 hover:text-indigo-500 dark:text-indigo-400 dark:hover:text-indigo-300">Sign in with Google</a>';
      } else {
        errorDiv.textContent =
          (data && data.detail) ||
          `An error occurred: ${response.statusText || response.status}`;
      }
      errorDiv.style.display = "block";
    } else {
      window.location.href = "/";
    }
  } catch (error) {
    console.error("Signup fetch/network error:", error);
    showError(
      "A network error occurred. Please check your connection and try again."
    );
  } finally {
    signupButton.disabled = false;
    signupText.style.display = "inline";
    signupSpinner.style.display = "none";
  }
});

function validateEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function validatePassword(password) {
  if (password.length < 8) return false;
  return (
    /[A-Z]/.test(password) &&
    /[a-z]/.test(password) &&
    /\d/.test(password) &&
    /[!@#$%^&*(),.?":{}|<>]/.test(password)
  );
}

function showError(message) {
  errorDiv.textContent = message;
  errorDiv.style.display = "block";
}
