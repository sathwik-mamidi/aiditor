const state = {
  ui: {
    isMobile: window.innerWidth < 768,
    isLoading: false,
  },
  admin: {
    targetUser: null,
    targetUserChats: {},
    activeChatId: null,
    activeChatMessages: [],
  },
  theme:
    localStorage.getItem("theme") ||
    (window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light"),
};

const API_BASE_URL = "/api";

class AdminApiClient {
  constructor(baseUrl = API_BASE_URL) {
    this.baseUrl = baseUrl;
    this.isRefreshing = false;
  }

  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    try {
      const response = await fetch(url, {
        ...options,
        credentials: "include",
      });

      if (response.status === 401 && !this.isRefreshing) {
        console.warn(
          "Admin API request unauthorized. Implement token refresh or ensure admin session is valid."
        );
        window.location.href = "/signin";
        return null;
      }

      if (!response.ok) {
        let errorDetail = "Request failed";
        try {
          const error = await response.json();
          errorDetail = error.detail || JSON.stringify(error);
        } catch (e) {
          errorDetail = response.statusText || `HTTP Error ${response.status}`;
        }
        throw new Error(errorDetail);
      }
      return response.status === 204 ? null : response.json();
    } catch (error) {
      console.error("Admin API Client Error:", error);
      if (error.message.includes("401") || error.message.includes("403")) {
        window.location.href = "/signin";
      }
      throw error;
    }
  }

  async getUserDetailsByEmail(email) {
    return this.request(
      `/admin/user-details-by-email?email=${encodeURIComponent(email)}`
    );
  }

  async getUserChats(targetUserId) {
    return this.request(`/admin/user-chats/${targetUserId}`);
  }

  async getChatDetails(chatId) {
    return this.request(`/admin/chat-details/${chatId}`);
  }

  async getSecureFileAccessDetails(fileId) {
    return this.request(`/admin/file-access-url/${fileId}`);
  }

  async logout() {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "include" });
    } finally {
      window.location.href = "/signin";
    }
  }
}

const adminApi = new AdminApiClient();

function getElement(id) {
  return document.getElementById(id);
}

function createElement(tag, attributes = {}, children = []) {
  const element = document.createElement(tag);
  Object.entries(attributes).forEach(([key, value]) => {
    if (key === "className") element.className = value;
    else if (key === "innerHTML") element.innerHTML = value;
    else if (key === "textContent") element.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.substring(2).toLowerCase(), value);
    } else element.setAttribute(key, value);
  });
  if (Array.isArray(children)) {
    children.forEach((child) => {
      if (child instanceof Node) element.appendChild(child);
      else if (child != null)
        element.appendChild(document.createTextNode(String(child)));
    });
  }
  return element;
}

const fileViewerModal = getElement("fileViewerModal");
const fileModalTitle = getElement("fileModalTitle");
const fileModalContent = getElement("fileModalContent");
const closeFileModalBtn = getElement("closeFileModalBtn");
const fileModalDownloadBtn = getElement("fileModalDownloadBtn");

function openFileModal(fileDetails) {
  console.log("File details for modal:", fileDetails);
  if (
    !fileViewerModal ||
    !fileModalTitle ||
    !fileModalContent ||
    !fileModalDownloadBtn
  ) {
    console.error("File modal elements not found!");
    return;
  }

  fileModalTitle.textContent = fileDetails.originalName || "File Viewer";
  fileModalContent.innerHTML = "";
  fileModalDownloadBtn.classList.add("hidden");

  if (fileDetails.downloadUrl) {
    fileModalDownloadBtn.href = fileDetails.downloadUrl;
    fileModalDownloadBtn.download = fileDetails.originalName || "download";
    fileModalDownloadBtn.classList.remove("hidden");
  } else if (fileDetails.displayUrl) {
    fileModalDownloadBtn.href = fileDetails.displayUrl;
    fileModalDownloadBtn.download = fileDetails.originalName || "download";
    fileModalDownloadBtn.classList.remove("hidden");
  }

  if (fileDetails.type === "image" && fileDetails.displayUrl) {
    const img = createElement("img", {
      src: fileDetails.displayUrl,
      alt: fileDetails.originalName,
      style: "max-width: 100%; max-height: 75vh; object-fit: contain;",
    });
    fileModalContent.appendChild(img);
  } else if (fileDetails.type === "video" && fileDetails.displayUrl) {
    const video = createElement("video", {
      src: fileDetails.displayUrl,
      controls: true,
      style: "max-width: 100%; max-height: 75vh;",
    });
    fileModalContent.appendChild(video);
  } else if (fileDetails.type === "audio" && fileDetails.displayUrl) {
    const audio = createElement("audio", {
      src: fileDetails.displayUrl,
      controls: true,
      style: "width: 100%;",
    });
    fileModalContent.appendChild(audio);
  } else if (fileDetails.displayUrl) {
    if (
      fileDetails.type === "code" ||
      fileDetails.type === "text" ||
      fileDetails.type === "log"
    ) {
      fileModalContent.style.alignItems = "flex-start";
      fileModalContent.style.justifyContent = "flex-start";

      fetch(fileDetails.displayUrl)
        .then((response) => {
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          return response.text();
        })
        .then((data) => {
          const pre = createElement("pre", {
            style:
              "white-space: pre-wrap; overflow-x: auto; font-family: monospace;",
            textContent: data,
          });
          fileModalContent.appendChild(pre);
        })
        .catch((error) => {
          console.error(
            "There was a problem with your fetch operation:",
            error
          );
          fileModalContent.textContent = "Could not load file content.";
        });
    } else {
      fileModalContent.appendChild(
        createElement("p", { style: "text-align: center;" }, [
          "This file type may not be previewable directly. ",
          createElement("a", {
            href: fileDetails.downloadUrl || fileDetails.displayUrl,
            target: "_blank",
            download: fileDetails.originalName,
            style: "color: #6366f1; text-decoration: underline;",
            textContent: `Download ${fileDetails.originalName}`,
          }),
        ])
      );
    }
  } else {
    fileModalContent.textContent =
      "Could not load file preview. URL missing or invalid.";
  }

  fileViewerModal.classList.remove("hidden");
  fileViewerModal.style.display = "flex";
}

function closeFileModal() {
  if (!fileViewerModal) return;
  fileViewerModal.classList.add("hidden");
  fileViewerModal.style.display = "none";
  if (fileModalContent) {
    fileModalContent.innerHTML = "";
    fileModalContent.style.alignItems = "center";
    fileModalContent.style.justifyContent = "center";
  }
  if (fileModalDownloadBtn) {
    fileModalDownloadBtn.href = "#";
    fileModalDownloadBtn.classList.add("hidden");
  }
}

function showAdminError(message) {
  const errorContainer = getElement("adminErrorContainer");
  if (errorContainer) {
    errorContainer.textContent = message;
    errorContainer.classList.remove("hidden");
  }
}

function hideAdminError() {
  const errorContainer = getElement("adminErrorContainer");
  if (errorContainer) {
    errorContainer.classList.add("hidden");
    errorContainer.textContent = "";
  }
}

async function handleViewTextFileGeneric(
  fileIdentifier,
  defaultFileName,
  fileTypeHint
) {
  if (!fileIdentifier) {
    showAdminError(`Cannot view file: Identifier missing.`);
    return;
  }
  console.log(`Attempting to view ${fileTypeHint}: ${fileIdentifier}`);

  try {
    const accessDetails = await adminApi.getSecureFileAccessDetails(
      fileIdentifier
    );

    let type = fileTypeHint;
    let originalName = defaultFileName;

    if (accessDetails.original_filename) {
      originalName = accessDetails.original_filename;
    }

    if (!accessDetails.url) {
      showAdminError(`Could not get a display URL for ${originalName}.`);
      return;
    }

    openFileModal({
      displayUrl: accessDetails.url,
      downloadUrl: accessDetails.download_url || accessDetails.url,
      originalName: originalName,
      type: type,
      file_id: accessDetails.file_id || fileIdentifier,
      mime_type: accessDetails.mime_type,
    });
  } catch (error) {
    console.error(
      `Failed to get secure access details for ${fileTypeHint} ${fileIdentifier}:`,
      error
    );
    showAdminError(
      `Error loading ${fileTypeHint} '${defaultFileName || fileIdentifier}': ${
        error.message
      }`
    );
  }
}

function updateTargetUserHeader(user) {
  const planCreditsSpan = getElement("targetUserPlanCredits");
  if (planCreditsSpan) {
    if (user) {
      const planName = user.plan || "free";
      const formattedPlan =
        planName.charAt(0).toUpperCase() + planName.slice(1);
      planCreditsSpan.textContent = `Target: ${user.email} (${formattedPlan}, ${
        user.credits !== undefined ? user.credits : "N/A"
      } credits)`;
    } else {
      planCreditsSpan.textContent = "Target User: N/A";
    }
  }
}

function renderTargetUserChats() {
  const chatHistoryUl = getElement("debugChatHistory");
  if (!chatHistoryUl) return;
  chatHistoryUl.innerHTML = "";

  if (
    !state.admin.targetUser ||
    Object.keys(state.admin.targetUserChats).length === 0
  ) {
    chatHistoryUl.appendChild(
      createElement("li", {
        className: "chat-item-placeholder",
        textContent: "No chats found for this user, or user not searched.",
      })
    );
    return;
  }

  const sortedChats = Object.values(state.admin.targetUserChats).sort(
    (a, b) =>
      new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  sortedChats.forEach((chat) => {
    const isActive = chat.chat_id === state.admin.activeChatId;
    const chatName = chat.chat_name || "Untitled Chat";
    const item = createElement("li", {}, [
      createElement("a", {
        href: "#",
        className: `chat-item ${isActive ? "chat-item-active" : ""}`,
        textContent: chatName,
        style: `
          display: block; 
          padding: 0.5rem; 
          border-radius: 0.5rem; 
          text-decoration: none; 
          color: inherit;
          transition: background-color 0.2s ease;
          ${
            isActive
              ? "background-color: var(--gray-200); font-weight: 600;"
              : ""
          }
        `,
        onclick: (e) => {
          e.preventDefault();
          if (state.admin.activeChatId !== chat.chat_id) {
            loadChatDetails(chat.chat_id);
          }
        },
        onmouseover: (e) => {
          if (!isActive) {
            e.target.style.backgroundColor = "var(--gray-100)";
          }
        },
        onmouseout: (e) => {
          if (!isActive) {
            e.target.style.backgroundColor = "transparent";
          }
        },
      }),
    ]);
    chatHistoryUl.appendChild(item);
  });
}

async function prepareFileForDisplayAdmin(fileData) {
  const fileId = fileData.file_id || fileData.fileId || fileData.id;
  const contextualOriginalName = fileData.original_filename;

  if (!fileId) {
    console.error(
      "File ID missing in fileData for prepareFileForDisplayAdmin",
      fileData
    );
    return {
      displayUrl: "",
      downloadUrl: "",
      thumbnailUrl: "",
      originalName: contextualOriginalName || "Unknown File",
      type: "other",
      file_id: null,
      mime_type: null,
    };
  }

  try {
    const accessDetails = await adminApi.getSecureFileAccessDetails(fileId);

    let type = "other";
    if (accessDetails.mime_type) {
      const mime = accessDetails.mime_type;
      if (mime.startsWith("image/")) type = "image";
      else if (mime.startsWith("video/")) type = "video";
      else if (mime.startsWith("audio/")) type = "audio";
    }

    return {
      displayUrl: accessDetails.url,
      downloadUrl: accessDetails.download_url,
      thumbnailUrl: accessDetails.thumbnail_url,
      originalName:
        contextualOriginalName ||
        accessDetails.original_filename ||
        "Unknown File",
      type: type,
      file_id: accessDetails.file_id,
      mime_type: accessDetails.mime_type,
    };
  } catch (error) {
    console.error(
      `Failed to get secure access details for file ${fileId}:`,
      error
    );
    showAdminError(
      `Error loading file ${contextualOriginalName || fileId}: ${error.message}`
    );
    return {
      displayUrl: "",
      downloadUrl: "",
      thumbnailUrl: "",
      originalName: contextualOriginalName || fileId,
      type: "other",
      file_id: fileId,
      error: true,
      mime_type: null,
    };
  }
}

async function renderFileGridAdmin(files, containerClassName = "") {
  const filesDiv = createElement("div", {
    style:
      "display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.5rem;",
    className: containerClassName,
  });
  if (!files || files.length === 0) return filesDiv;

  for (const fileInfo of files) {
    const {
      displayUrl,
      downloadUrl,
      thumbnailUrl,
      originalName,
      type,
      file_id,
      mime_type,
      error,
    } = await prepareFileForDisplayAdmin(fileInfo);

    const previewDiv = createElement("div", {
      style: `
        width: 6rem; 
        height: 6rem; 
        border-radius: 0.5rem; 
        overflow: hidden; 
        border: 1px solid var(--gray-200); 
        display: flex; 
        align-items: center; 
        justify-content: center; 
        background-color: var(--gray-100); 
        cursor: pointer;
        font-size: 0.75rem;
        text-align: center;
      `,
      title: originalName,
      onclick: () => {
        if (error) {
          showAdminError(
            `Cannot open file: ${originalName}. Details failed to load.`
          );
          return;
        }
        if (displayUrl || thumbnailUrl) {
          openFileModal({
            displayUrl,
            downloadUrl,
            thumbnailUrl,
            originalName,
            type,
            file_id,
            mime_type,
          });
        } else {
          showAdminError(`No display URL available for ${originalName}.`);
        }
      },
    });

    let previewSrc = thumbnailUrl;
    if (type === "image" && !thumbnailUrl) {
      previewSrc = displayUrl;
    }

    if (type === "image" && previewSrc) {
      previewDiv.appendChild(
        createElement("img", {
          src: previewSrc,
          alt: originalName,
          style: "width: 100%; height: 100%; object-fit: cover;",
        })
      );
    } else if (type === "video" && thumbnailUrl) {
      const videoContainerDiv = createElement("div", {
        style: "position: relative; width: 100%; height: 100%;",
      });
      videoContainerDiv.appendChild(
        createElement("img", {
          src: thumbnailUrl,
          alt: originalName,
          style:
            "width: 100%; height: 100%; object-fit: contain; background-color: var(--gray-100);",
        })
      );
      videoContainerDiv.appendChild(
        createElement("div", {
          style:
            "position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;",
          innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" style="height: 2rem; width: 2rem; color: white; filter: drop-shadow(0 4px 3px rgba(0, 0, 0, 0.07));" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
        })
      );
      previewDiv.appendChild(videoContainerDiv);
    } else if (type === "audio") {
      previewDiv.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" style="height: 2.5rem; width: 2.5rem; color: var(--gray-400);" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" /></svg>`;
    } else {
      previewDiv.textContent =
        originalName.split(".").pop()?.toUpperCase() || "FILE";
    }
    filesDiv.appendChild(previewDiv);
  }
  return filesDiv;
}

async function renderDebugMessages() {
  const messagesContainer = getElement("debugMessagesContainer");
  if (!messagesContainer) return;
  messagesContainer.innerHTML = "";

  if (state.admin.activeChatMessages.length === 0) {
    messagesContainer.appendChild(
      createElement("p", {
        className: "messages-placeholder",
        textContent: state.admin.activeChatId
          ? "No messages in this chat."
          : "Select a chat to view its history or search for a user.",
      })
    );
    return;
  }

  for (const conv of state.admin.activeChatMessages) {
    const messageDiv = createElement("div", {
      style: `
        display: flex; 
        flex-direction: column; 
        ${
          conv.role === "user"
            ? "align-items: flex-end;"
            : "align-items: flex-start;"
        } 
        margin-bottom: 1rem;
      `,
    });

    const contentDiv = createElement("div", {
      style: `
        max-width: 48rem; 
        border-radius: 0.5rem; 
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1); 
        padding: 0.75rem;
        ${
          conv.role === "user"
            ? "background-color: var(--indigo-600); color: white; margin-left: auto;"
            : "background-color: var(--gray-200); color: var(--gray-900); margin-right: auto;"
        }
      `,
    });

    if (conv.input_files && conv.input_files.length > 0) {
      contentDiv.appendChild(await renderFileGridAdmin(conv.input_files));
    }
    if (conv.prompt) {
      contentDiv.appendChild(
        createElement("p", {
          textContent: conv.prompt,
          style: "margin-top: 0.25rem;",
        })
      );
    }

    if (conv.role === "assistant") {
      if (conv.response?.message) {
        contentDiv.appendChild(
          createElement("p", {
            textContent: conv.response.message,
            style:
              conv.output_files?.length > 0 ||
              conv.response?.code ||
              conv.response?.log
                ? "margin-bottom: 0.5rem;"
                : "",
          })
        );
      }

      const responseAreaDiv = createElement("div");

      if (conv.output_files && conv.output_files.length > 0) {
        const fileGrid = await renderFileGridAdmin(conv.output_files);
        if (fileGrid.children.length > 0 && conv.response?.message) {
          fileGrid.style.marginTop = "0.5rem";
        }
        responseAreaDiv.appendChild(fileGrid);
      }

      const hasCodeOrLog = conv.response?.code || conv.response?.log;
      if (hasCodeOrLog) {
        const linksContainer = createElement("div", {
          style:
            "margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid var(--gray-300); display: flex; align-items: center; gap: 1rem;",
        });

        if (conv.response.code) {
          const codeFileName =
            conv.response.code.split("/").pop() || "script.py";
          linksContainer.appendChild(
            createElement("a", {
              href: "#",
              textContent: "View Code",
              style:
                "font-size: 0.75rem; color: var(--indigo-400); text-decoration: none;",
              onclick: (e) => {
                e.preventDefault();
                handleViewTextFileGeneric(
                  conv.response.code,
                  codeFileName,
                  "code"
                );
              },
              onmouseover: (e) => {
                e.target.style.color = "var(--indigo-600)";
              },
              onmouseout: (e) => {
                e.target.style.color = "var(--indigo-400)";
              },
            })
          );
        }

        if (conv.response.log) {
          const logFileName =
            conv.response.log.split("/").pop() || "details.log";
          linksContainer.appendChild(
            createElement("a", {
              href: "#",
              textContent: "View Log",
              style:
                "font-size: 0.75rem; color: var(--indigo-400); text-decoration: none;",
              onclick: (e) => {
                e.preventDefault();
                handleViewTextFileGeneric(
                  conv.response.log,
                  logFileName,
                  "log"
                );
              },
              onmouseover: (e) => {
                e.target.style.color = "var(--indigo-600)";
              },
              onmouseout: (e) => {
                e.target.style.color = "var(--indigo-400)";
              },
            })
          );
        }
        responseAreaDiv.appendChild(linksContainer);
      }

      if (responseAreaDiv.children.length > 0) {
        contentDiv.appendChild(responseAreaDiv);
      }

      if (
        conv.status === "failed" &&
        conv.response?.message &&
        !hasCodeOrLog &&
        !conv.output_files?.length > 0
      ) {
        contentDiv.appendChild(
          createElement("p", {
            textContent: `Status: FAILED - ${conv.response.message}`,
            style:
              "font-size: 0.75rem; color: var(--red-400); margin-top: 0.25rem;",
          })
        );
      } else if (conv.status === "failed") {
        contentDiv.appendChild(
          createElement("p", {
            textContent: "Status: FAILED",
            style:
              "font-size: 0.75rem; color: var(--red-400); margin-top: 0.25rem;",
          })
        );
      }
    }
    messageDiv.appendChild(contentDiv);
    messagesContainer.appendChild(messageDiv);
  }
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

async function handleUserSearch(event) {
  event.preventDefault();
  hideAdminError();
  const emailInput = getElement("userEmailInput");
  const email = emailInput.value.trim();
  if (!email) {
    showAdminError("Please enter a user email.");
    return;
  }

  state.ui.isLoading = true;
  state.admin.targetUser = null;
  state.admin.targetUserChats = {};
  state.admin.activeChatId = null;
  state.admin.activeChatMessages = [];
  updateTargetUserHeader(null);
  renderTargetUserChats();
  renderDebugMessages();

  try {
    const userDetails = await adminApi.getUserDetailsByEmail(email);
    state.admin.targetUser = userDetails;
    updateTargetUserHeader(userDetails);

    if (userDetails.user_id) {
      const chats = await adminApi.getUserChats(userDetails.user_id);
      state.admin.targetUserChats = chats.reduce((acc, chat) => {
        acc[chat.chat_id] = chat;
        return acc;
      }, {});
      renderTargetUserChats();
    } else {
      showAdminError("User found, but ID missing in details.");
    }
  } catch (error) {
    console.error("Error fetching user or chats:", error);
    showAdminError(error.message || "Failed to fetch user data.");
    updateTargetUserHeader(null);
  }
  state.ui.isLoading = false;
}

async function loadChatDetails(chatId) {
  if (!chatId || !state.admin.targetUser) return;
  hideAdminError();
  state.ui.isLoading = true;
  state.admin.activeChatId = chatId;
  state.admin.activeChatMessages = [];
  renderDebugMessages();
  renderTargetUserChats();

  try {
    const chatDetails = await adminApi.getChatDetails(chatId);
    state.admin.activeChatMessages = chatDetails.conversations || [];
    renderDebugMessages();
  } catch (error) {
    console.error("Error fetching chat details:", error);
    showAdminError(error.message || "Failed to load chat details.");
    state.admin.activeChatId = null;
    renderTargetUserChats();
  }
  state.ui.isLoading = false;
}

function initializeThemeToggle() {
  const themeToggleBtn = getElement("themeToggle");
  const body = document.body;

  const updateToggleButton = () => {
    const isDark = body.classList.contains("dark");
    if (themeToggleBtn) {
      themeToggleBtn.innerHTML = isDark
        ? `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="icon">
             <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
           </svg>`
        : `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="icon">
             <path stroke-linecap="round" stroke-linejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
           </svg>`;
    }
  };

  const setTheme = (theme) => {
    body.classList.toggle("dark", theme === "dark");
    localStorage.setItem("theme", theme);
    state.theme = theme;
    updateToggleButton();
  };

  // Initialize theme
  if (state.theme === "dark") {
    body.classList.add("dark");
  }
  setTheme(state.theme);

  if (themeToggleBtn) {
    themeToggleBtn.addEventListener("click", () => {
      const newTheme = body.classList.contains("dark") ? "light" : "dark";
      setTheme(newTheme);
    });
  }

  // Listen for system theme changes
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (e) => {
      if (!localStorage.getItem("theme")) {
        setTheme(e.matches ? "dark" : "light");
      }
    });
}

function initializeDebugPage() {
  const userSearchForm = getElement("userSearchForm");
  if (userSearchForm) {
    userSearchForm.addEventListener("submit", handleUserSearch);
  }

  const logoutBtn = getElement("logoutBtn");
  if (logoutBtn) {
    logoutBtn.addEventListener("click", () => adminApi.logout());
  }

  if (closeFileModalBtn) {
    closeFileModalBtn.addEventListener("click", closeFileModal);
  }
  if (fileViewerModal) {
    fileViewerModal.addEventListener("click", (event) => {
      if (event.target === fileViewerModal) {
        closeFileModal();
      }
    });
  }

  initializeThemeToggle();
  updateTargetUserHeader(null);
  renderTargetUserChats();
  renderDebugMessages();

  // Cookie Consent Banner Logic
  const cookieConsentBanner = getElement("cookieConsentBanner");
  const acceptCookieConsentButton = getElement("acceptCookieConsent");

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
}

document.addEventListener("DOMContentLoaded", initializeDebugPage);
