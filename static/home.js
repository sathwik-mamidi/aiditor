// Inactive-build notice: intercept sign-up/pricing CTAs since accounts
// aren't being created right now, instead of sending people to a dead route.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".signup-cta").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      showNotification(
        "Aiditor isn't accepting new accounts right now — this build is archived. Watch the demo instead.",
        "info"
      );
      if (typeof posthog !== "undefined") {
        posthog.capture("inactive_signup_cta_clicked");
      }
    });
  });
});

// Restore scroll position on reload
document.addEventListener("DOMContentLoaded", function () {
  const scrollPosition = sessionStorage.getItem("scrollPosition");
  if (scrollPosition) {
    window.scrollTo(0, parseInt(scrollPosition));
  }
});

// Save scroll position before page unload
window.addEventListener("beforeunload", function () {
  sessionStorage.setItem("scrollPosition", window.scrollY);
});

// Cookie Consent Banner Logic
document.addEventListener("DOMContentLoaded", () => {
  const cookieConsentBanner = document.getElementById("cookieConsentBanner");
  const acceptCookieConsentButton = document.getElementById(
    "acceptCookieConsent"
  );

  if (cookieConsentBanner && acceptCookieConsentButton) {
    // Show banner if consent hasn't been given
    if (localStorage.getItem("cookieConsent") !== "accepted") {
      setTimeout(() => {
        cookieConsentBanner.classList.remove("hidden");
      }, 500);
    }

    // Handle accept button click
    acceptCookieConsentButton.addEventListener("click", () => {
      localStorage.setItem("cookieConsent", "accepted");
      cookieConsentBanner.classList.add("hidden");
    });
  }
});

// Smooth scrolling for anchor links
document.addEventListener("DOMContentLoaded", () => {
  const anchorLinks = document.querySelectorAll('a[href^="#"]');

  anchorLinks.forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const targetId = link.getAttribute("href");

      // Use querySelector for more reliable element finding
      const targetElement = document.querySelector(targetId);

      if (targetElement) {
        // Use scrollIntoView for more consistent scrolling behavior
        targetElement.scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      }
    });
  });
});

// Lazy loading for images (if any images are added later)
document.addEventListener("DOMContentLoaded", () => {
  if ("IntersectionObserver" in window) {
    const imageObserver = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const img = entry.target;
          img.src = img.dataset.src;
          img.classList.remove("lazy");
          imageObserver.unobserve(img);
        }
      });
    });

    const lazyImages = document.querySelectorAll("img[data-src]");
    lazyImages.forEach((img) => imageObserver.observe(img));
  }
});

// Handle form submissions (if forms are added later)
function handleFormSubmission(formId, successMessage) {
  const form = document.getElementById(formId);
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();

      // Add loading state
      const submitButton = form.querySelector('button[type="submit"]');
      const originalText = submitButton.textContent;
      submitButton.textContent = "Loading...";
      submitButton.disabled = true;

      // Simulate form submission (replace with actual API call)
      setTimeout(() => {
        // Reset button
        submitButton.textContent = originalText;
        submitButton.disabled = false;

        // Show success message
        showNotification(successMessage, "success");

        // Reset form
        form.reset();
      }, 2000);
    });
  }
}

// Notification system
function showNotification(message, type = "info") {
  const notification = document.createElement("div");
  notification.className = `notification notification-${type}`;
  notification.textContent = message;

  // Add styles
  Object.assign(notification.style, {
    position: "fixed",
    top: "20px",
    right: "20px",
    padding: "14px 20px",
    maxWidth: "340px",
    borderRadius: "8px",
    color: "#faf7ef",
    fontFamily: "'Inter', sans-serif",
    fontSize: "14px",
    fontWeight: "500",
    lineHeight: "1.5",
    boxShadow: "0 12px 30px -8px rgba(24, 21, 15, 0.35)",
    zIndex: "1000",
    transform: "translateX(120%)",
    transition: "transform 0.3s ease",
    backgroundColor:
      type === "success" ? "#0e6b58" : type === "error" ? "#d5432a" : "#18150f",
  });

  document.body.appendChild(notification);

  // Animate in
  setTimeout(() => {
    notification.style.transform = "translateX(0)";
  }, 100);

  // Remove after 5 seconds
  setTimeout(() => {
    notification.style.transform = "translateX(100%)";
    setTimeout(() => {
      document.body.removeChild(notification);
    }, 300);
  }, 5000);
}

// Performance optimization: Debounce scroll events
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

// Handle scroll events with debouncing
let isScrolling = false;
const handleScroll = debounce(() => {
  isScrolling = false;
}, 100);

window.addEventListener("scroll", () => {
  if (!isScrolling) {
    isScrolling = true;
    // Add any scroll-based functionality here
  }
  handleScroll();
});

// Accessibility improvements
document.addEventListener("DOMContentLoaded", () => {
  // Add keyboard navigation for custom elements
  const interactiveElements = document.querySelectorAll(
    '[role="button"], .btn'
  );

  interactiveElements.forEach((element) => {
    element.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        element.click();
      }
    });

    // Ensure elements are focusable
    if (!element.hasAttribute("tabindex")) {
      element.setAttribute("tabindex", "0");
    }
  });
});

// Error handling for failed requests
window.addEventListener("error", (e) => {
  console.error("JavaScript error:", e.error);
  // You could send error reports to a logging service here
});

// Handle online/offline status
window.addEventListener("online", () => {
  showNotification("Connection restored", "success");
});

window.addEventListener("offline", () => {
  showNotification("Connection lost. Some features may not work.", "error");
});

// Initialize any additional functionality
document.addEventListener("DOMContentLoaded", () => {
  // Mobile navigation toggle
  const navToggle = document.getElementById("navToggle");
  const mobileNav = document.getElementById("mobileNav");

  if (navToggle && mobileNav) {
    const closeMobileNav = () => {
      mobileNav.classList.remove("open");
      mobileNav.classList.add("hidden");
      navToggle.classList.remove("open");
      navToggle.setAttribute("aria-label", "Open menu");
    };

    const openMobileNav = () => {
      mobileNav.classList.add("open");
      mobileNav.classList.remove("hidden");
      navToggle.classList.add("open");
      navToggle.setAttribute("aria-label", "Close menu");
    };

    navToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      if (mobileNav.classList.contains("open")) {
        closeMobileNav();
      } else {
        openMobileNav();
      }
    });

    // Close menu when clicking outside
    document.addEventListener("click", (e) => {
      if (
        mobileNav.classList.contains("open") &&
        !mobileNav.contains(e.target) &&
        !navToggle.contains(e.target)
      ) {
        closeMobileNav();
      }
    });

    // Close menu when a link or button inside it is clicked
    mobileNav.querySelectorAll("a, button").forEach((el) => {
      el.addEventListener("click", () => {
        closeMobileNav();
      });
    });

    // Close menu on escape key
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && mobileNav.classList.contains("open")) {
        closeMobileNav();
      }
    });
  }
  console.log("AIDITOR website loaded successfully");

  // Track page load time for performance monitoring
  window.addEventListener("load", () => {
    const loadTime = performance.now();
    console.log(`Page loaded in ${Math.round(loadTime)}ms`);
  });
});

// Card tilt micro-interaction and pop-in animation
function addCardTiltAndPopIn() {
  const cardSelectors = [
    ".demo-card",
    ".capability-card",
    ".problem-card",
    ".use-case",
    ".faq-item",
    ".pricing-card",
    ".about-stat",
  ];
  const cards = document.querySelectorAll(cardSelectors.join(","));

  cards.forEach((card) => {
    // Pop-in animation
    card.classList.add("animate-pop-in");

    // Subtle tilt effect
    card.addEventListener("mousemove", (e) => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      const centerX = rect.width / 2;
      const centerY = rect.height / 2;
      const rotateX = ((y - centerY) / centerY) * 2.5;
      const rotateY = ((x - centerX) / centerX) * 3;
      card.style.transform = `perspective(600px) rotateX(${-rotateX}deg) rotateY(${rotateY}deg) scale(1.01)`;
    });
    card.addEventListener("mouseleave", () => {
      card.style.transform = "";
    });
  });
}

document.addEventListener("DOMContentLoaded", addCardTiltAndPopIn);

// Demo Video Modal Functionality
function openDemoModal() {
  const modal = document.getElementById("demoModal");
  if (modal) {
    modal.classList.add("show");
    modal.style.display = "flex";
    document.body.style.overflow = "hidden"; // Prevent background scrolling

    // Track demo video views
    if (typeof posthog !== "undefined") {
      posthog.capture("demo_video_opened");
    }
  }
}

function closeDemoModal() {
  const modal = document.getElementById("demoModal");
  if (modal) {
    modal.classList.remove("show");
    setTimeout(() => {
      modal.style.display = "none";
    }, 300); // Wait for animation to complete
    document.body.style.overflow = ""; // Restore scrolling

    // Stop video playback by reloading the iframe
    const iframe = modal.querySelector("iframe");
    if (iframe) {
      const src = iframe.src;
      iframe.src = "";
      iframe.src = src;
    }
  }
}

// Initialize modal event listeners
document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("demoModal");
  const closeButton = document.querySelector(".close-button");

  if (modal && closeButton) {
    // Close modal when clicking the close button
    closeButton.addEventListener("click", closeDemoModal);

    // Close modal when clicking outside the modal content
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        closeDemoModal();
      }
    });

    // Close modal when pressing Escape key
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && modal.classList.contains("show")) {
        closeDemoModal();
      }
    });
  }
});

// Keep the existing scrollToDemo function for backwards compatibility
function scrollToDemo() {
  document.getElementById("demo").scrollIntoView({ behavior: "smooth" });
}
