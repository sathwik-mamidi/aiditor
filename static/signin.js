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

const signinForm = document.getElementById("signin-form");
const errorDiv = document.getElementById("error-message");
const signinButton = document.getElementById("signin-button");
const signinText = document.getElementById("signin-text");
const signinSpinner = document.getElementById("signin-spinner");

signinForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  signinButton.disabled = true;
  signinText.style.display = "none";
  signinSpinner.style.display = "inline-block";

  const formData = new FormData(signinForm);
  const bodyData = new URLSearchParams();
  for (const pair of formData.entries()) {
    bodyData.append(pair[0], pair[1]);
  }

  try {
    const response = await fetch("/signin", {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
      body: bodyData,
    });

    const result = await response.json();

    if (response.ok) {
      window.location.href = "/";
    } else {
      if (result.detail?.includes("Google Sign-In")) {
        errorDiv.innerHTML =
          result.detail +
          ' <a href="/auth/google" class="signin-link">Sign in with Google</a>';
      } else if (result.detail === "Incorrect email or password") {
        errorDiv.textContent = "Incorrect email or password.";
      } else if (result.detail === "User not registered") {
        errorDiv.innerHTML =
          'User not registered. <a href="/signup" class="signin-link">Sign up instead</a>';
      } else {
        errorDiv.textContent =
          result.detail || "Signin failed. Please check your credentials.";
      }
      errorDiv.style.display = "block";
    }
  } catch (error) {
    console.error("Signin error:", error);
    errorDiv.textContent = "An unexpected error occurred. Please try again.";
    errorDiv.style.display = "block";
  } finally {
    signinButton.disabled = false;
    signinText.style.display = "inline";
    signinSpinner.style.display = "none";
  }
});
