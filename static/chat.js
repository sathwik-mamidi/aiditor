// Application State
const state = {
  ui: {
    isSidebarOpen: false,
    isMobile: window.innerWidth < 768,
    isLoading: false,
    dragOver: false,
  },
  user: { plan: "free", credits: 0, email: "", user_id: null },
  chat: { all: {}, messages: [], active: null, current: null },
  files: { list: [], previews: [] },
  activeTimers: {},
  activeGlobalThumbnailPollers: {},
  activeInputThumbnailPollers: {}, // Added for input preview thumbnail polling
};

// Constants
const API_BASE_URL = "/api";
const MAX_THUMBNAIL_POLLS = 5;
const THUMBNAIL_POLL_INTERVAL = 3000;
const POLLING_INTERVAL = 10000;
const MAX_POLLING_ATTEMPTS = 100;
const MAX_GLOBAL_THUMBNAIL_POLLING_ATTEMPTS = 20;

/**
 * API Client for handling all server communications
 */
class ApiClient {
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
        const refreshedResponse = await this.handleTokenRefresh(url, options);
        if (refreshedResponse) return refreshedResponse;
      }

      if (!response.ok) {
        let errorDetail = "Request failed";
        try {
          const error = await response.json();
          errorDetail = error.detail || JSON.stringify(error);
        } catch (e) {
          errorDetail = response.statusText || `HTTP Error ${response.status}`;
        }

        if (response.status === 401) {
          window.location.href = "/signin";
          return null;
        }

        throw new Error(errorDetail);
      }

      return response.status === 204 ? null : response.json();
    } catch (error) {
      if (
        error.message === "Failed to fetch" ||
        error.message.includes("NetworkError")
      ) {
        window.location.href = "/signin";
        return null;
      }
      throw error;
    }
  }

  async handleTokenRefresh(originalUrl, originalOptions) {
    try {
      this.isRefreshing = true;
      const refreshResponse = await fetch("/auth/refresh", {
        method: "POST",
        credentials: "include",
      });

      if (!refreshResponse.ok) {
        window.location.href = "/signin";
        return null;
      }

      const response = await fetch(originalUrl, {
        ...originalOptions,
        credentials: "include",
      });

      if (!response.ok) {
        throw new Error("Request failed after token refresh");
      }

      return response.status === 204 ? null : response.json();
    } finally {
      this.isRefreshing = false;
    }
  }

  async uploadFiles(files, chatId) {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    if (chatId) formData.append("chat_id", chatId);
    return this.request("/upload", { method: "POST", body: formData });
  }

  async createChat(prompt, filePaths = [], chatId = null) {
    const formData = new FormData();
    if (prompt) {
      // Ensure prompt is only added if it exists, to match backend Optional[str] = Form(None)
      formData.append("prompt", prompt);
    }
    if (chatId) {
      formData.append("chat_id", chatId);
    }
    // filePaths are not directly sent here as send_message_to_chat uses get_pending_files.

    return this.request("/c", {
      // This POSTs to send_message_to_chat
      method: "POST",
      body: formData, // FastAPI will parse this as Form data
    });
  }

  async deleteFile(filename) {
    return this.request(`/upload/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    });
  }

  async getChat(chatId) {
    return this.request(`/c/${chatId}`);
  }

  async getUserChats() {
    return this.request(`/c`);
  }

  async deleteChat(chatId) {
    return this.request(`/c/${chatId}`, { method: "DELETE" });
  }

  async processChat(chatId) {
    return this.request(`/c/${chatId}/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}), // Send an empty object as the Pydantic model is now empty
    });
  }

  async getTaskStatus(taskId) {
    return this.request(`/c/task/${taskId}/status`);
  }

  async saveAssistantTurn(chatId, assistantTurnData) {
    return this.request(`/c/${chatId}/assistant_turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assistant_turn_data: assistantTurnData }),
    });
  }

  async getFileUrl(fileId, isThumbnail = false, forDownload = false) {
    let path = `/url/${fileId}`;
    const params = new URLSearchParams();
    if (isThumbnail) params.append("thumbnail", "true");
    if (forDownload) params.append("download", "true");
    if (params.toString()) path += `?${params.toString()}`;

    try {
      return await this.request(path);
    } catch (error) {
      console.error(`Error fetching file URL for ${fileId}:`, error);
      return null;
    }
  }

  async logout() {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "include" });
    } finally {
      window.location.href = "/signin";
    }
  }

  async getUserInfo() {
    return this.request("/user");
  }
}

// Initialize API client
const api = new ApiClient();

/**
 * DOM Helper Functions
 */
function createElement(tag, attributes = {}, children = []) {
  const element = document.createElement(tag);

  Object.entries(attributes).forEach(([key, value]) => {
    if (key === "className") {
      element.className = value;
    } else if (key === "innerHTML") {
      element.innerHTML = value;
    } else if (key === "textContent") {
      element.textContent = value;
    } else if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.substring(2).toLowerCase(), value);
    } else {
      element.setAttribute(key, value);
    }
  });

  if (Array.isArray(children)) {
    children.forEach((child) => {
      if (child instanceof Node) {
        element.appendChild(child);
      } else if (child != null) {
        element.appendChild(document.createTextNode(String(child)));
      }
    });
  }

  return element;
}

function getElement(id) {
  return document.getElementById(id);
}

function isPlaceholderThumbnail(thumbnailUrl) {
  return thumbnailUrl && thumbnailUrl.startsWith("data:image/svg+xml");
}

async function prepareFileForDisplay(fileData, requestThumbnailInChat = false) {
  const fileId = fileData.file_id || fileData.fileId;
  let displayUrl = "";
  let thumbnailUrl = null;
  let originalName =
    fileData.original_filename ||
    fileData.originalName ||
    fileData.name ||
    "Unknown File";
  let type = "other";

  if (fileData.metadata?.mime_type) {
    const mime = fileData.metadata.mime_type;
    if (mime.startsWith("image/")) type = "image";
    else if (mime.startsWith("video/")) type = "video";
    else if (mime.startsWith("audio/")) type = "audio";
  }

  if (type === "other" && fileData.type && fileData.type !== "other") {
    type = fileData.type;
  }

  if (type === "other" && fileData.original_filename) {
    const ext = (
      fileData.original_filename.split(".").pop() || ""
    ).toLowerCase();
    if (
      ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"].includes(ext)
    )
      type = "image";
    else if (["mp4", "mov", "avi", "wmv", "flv", "mkv", "webm"].includes(ext))
      type = "video";
    else if (["mp3", "wav", "ogg", "aac", "flac"].includes(ext)) type = "audio";
  }

  if (!fileId) {
    return { displayUrl, thumbnailUrl, originalName, type };
  }

  try {
    // Fetch main display URL
    // Only fetch if displayUrl isn't already provided and valid on fileData
    if (fileData.url && !isPlaceholderThumbnail(fileData.url)) {
      // Assuming placeholder check is also relevant for main URL if it can be a placeholder
      displayUrl = fileData.url;
      if (fileData.originalName) originalName = fileData.originalName; // Keep originalName if already resolved
    } else {
      const mainUrlResponse = await api.getFileUrl(fileId, false, false);
      if (mainUrlResponse && typeof mainUrlResponse.url === "string") {
        displayUrl = mainUrlResponse.url;
        if (typeof mainUrlResponse.filename === "string") {
          originalName = mainUrlResponse.filename;
        }
      }
    }

    if (type === "image") {
      if (requestThumbnailInChat) {
        // Check if a valid thumbnail URL already exists on fileData
        if (
          fileData.thumbnail &&
          !isPlaceholderThumbnail(fileData.thumbnail) &&
          fileData.thumbnail.includes("/thumbnails/")
        ) {
          thumbnailUrl = fileData.thumbnail;
        } else {
          const thumbUrlResponse = await api.getFileUrl(fileId, true, false);
          if (
            thumbUrlResponse &&
            typeof thumbUrlResponse.url === "string" &&
            thumbUrlResponse.url.trim() !== ""
          ) {
            thumbnailUrl = thumbUrlResponse.url;
          }
        }
      }
    } else if (type === "video") {
      if (requestThumbnailInChat) {
        // Check if a valid thumbnail URL already exists on fileData
        if (
          fileData.thumbnail &&
          !isPlaceholderThumbnail(fileData.thumbnail) &&
          fileData.thumbnail.includes("/thumbnails/")
        ) {
          thumbnailUrl = fileData.thumbnail;
        } else {
          const thumbUrlResponse = await api.getFileUrl(fileId, true, false);
          if (
            thumbUrlResponse &&
            typeof thumbUrlResponse.url === "string" &&
            thumbUrlResponse.url.trim() !== "" &&
            thumbUrlResponse.url.includes("/thumbnails/")
          ) {
            thumbnailUrl = thumbUrlResponse.url;
          } else {
            console.warn(
              `prepareFileForDisplay: api.getFileUrl for video thumbnail ${fileId} did not return a dedicated thumbnail URL. Using placeholder. Response:`,
              thumbUrlResponse
            );
            thumbnailUrl =
              "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='240' viewBox='0 0 320 240'%3E%3Crect width='320' height='240' fill='%23e5e7eb'/%3E%3Cpath d='M144 96v48l32-24-32-24z' fill='%239ca3af'/%3E%3C/svg%3E";
          }
        }
      } else {
        thumbnailUrl =
          "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='240' viewBox='0 0 320 240'%3E%3Crect width='320' height='240' fill='%23e5e7eb'/%3E%3Cpath d='M144 96v48l32-24-32-24z' fill='%239ca3af'/%3E%3C/svg%3E";
      }
    } else if (type === "audio") {
      // For audio, thumbnail isn't usually fetched unless explicitly designed.
      // If a specific thumbnail mechanism exists and requestThumbnailInChat is true:
      if (requestThumbnailInChat) {
        if (fileData.thumbnail && !isPlaceholderThumbnail(fileData.thumbnail)) {
          // Assuming audio might have a pre-set or polled thumbnail
          thumbnailUrl = fileData.thumbnail;
        } else {
          // Optionally, attempt to fetch if an audio thumbnail concept exists via API
          // const thumbUrlResponse = await api.getFileUrl(fileId, true, false);
          // if (thumbUrlResponse && typeof thumbUrlResponse.url === "string" && thumbUrlResponse.url.trim() !== "") {
          //   thumbnailUrl = thumbUrlResponse.url;
          // }
        }
      }
    }
  } catch (error) {
    console.error(`Error fetching URLs for fileId ${fileId}:`, error);
  }

  return { displayUrl, thumbnailUrl, originalName, type };
}

function createFilePreviewElement(file, options = {}) {
  const {
    isProcessing,
    isLoadingThumbnail,
    isRemovable = false,
    isDownloadable = false,
    onRemove,
    onClick,
  } = options;

  const fileDiv = createElement("div", {
    className: "file-preview-item", // New CSS class
    onClick,
  });

  const previewDiv = createElement("div", {
    className: "file-preview-content", // New CSS class
  });

  const isActuallyUploadingToS3 = isProcessing;
  const showSpinnerOnly = isActuallyUploadingToS3 || isLoadingThumbnail;

  if (showSpinnerOnly) {
    const spinnerClasses = ["file-preview-spinner"]; // New CSS class
    if (isLoadingThumbnail && !isActuallyUploadingToS3) {
      spinnerClasses.push("loading-thumbnail");
    } else if (isActuallyUploadingToS3) {
      spinnerClasses.push("uploading");
    } else {
      spinnerClasses.push("loading");
    }

    previewDiv.innerHTML = "";
    previewDiv.appendChild(
      createElement("div", {
        className: spinnerClasses.join(" "),
        innerHTML: `<div class="spinner"></div>`, // New CSS class
      })
    );
  } else {
    // Content rendering logic remains the same but with updated classes
    previewDiv.innerHTML = "";
    previewDiv.className = "file-preview-content loaded"; // Updated class

    if (file.type === "image" && file.thumbnail) {
      previewDiv.appendChild(
        createElement("img", {
          src: file.thumbnail,
          alt: "Preview",
          className: "file-preview-image", // New CSS class
        })
      );
    } else if (file.type === "video" && file.thumbnail) {
      const videoContainerDiv = createElement("div", {
        className: "file-preview-video", // New CSS class
      });
      videoContainerDiv.appendChild(
        createElement("img", {
          src: file.thumbnail,
          alt: "Video thumbnail",
          className: "file-preview-video-thumb", // New CSS class
        })
      );
      videoContainerDiv.appendChild(
        createElement("div", {
          className: "file-preview-play-button", // New CSS class
          innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" class="play-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>`,
        })
      );
      previewDiv.appendChild(videoContainerDiv);
    } else if (file.type === "audio") {
      previewDiv.className += " audio-placeholder"; // Add to existing classes
      previewDiv.appendChild(
        createElement("div", {
          className: "file-preview-audio", // New CSS class
          innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" class="audio-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" /></svg>`,
        })
      );
    } else {
      previewDiv.className += " file-placeholder"; // Add to existing classes
      previewDiv.appendChild(
        createElement("div", {
          className: "file-preview-generic", // New CSS class
          textContent: file.originalName
            ? file.originalName.split(".").pop()?.toUpperCase()
            : "FILE",
        })
      );
    }
  }

  fileDiv.appendChild(previewDiv);

  if (isDownloadable) {
    previewDiv.appendChild(
      createElement("div", {
        className: "file-download-button", // New CSS class
        innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" class="download-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
        </svg>`,
      })
    );
  }

  if (isRemovable) {
    const removeButton = createElement("button", {
      className: "file-remove-button", // New CSS class
      innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" class="remove-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>`,
      onclick: async (e) => {
        e.stopPropagation();
        const identifier = file.file_id || file.originalName;
        state.files.previews = state.files.previews.filter(
          (p) => (p.file_id || p.originalName) !== identifier
        );
        renderFilePreviews();
        if (onRemove) {
          onRemove(file).catch((error) => {
            console.error("Error in onRemove callback execution:", error);
          });
        }
      },
    });
    fileDiv.appendChild(removeButton);
  }

  return fileDiv;
}

async function transformConversationsToMessages(apiConversations) {
  if (!Array.isArray(apiConversations)) return [];

  const messages = [];

  for (const conv of apiConversations) {
    if (conv.role === "user") {
      const filesToProcessForUser = conv.input_files || [];
      const files = await Promise.all(
        filesToProcessForUser.map(async (fileInfo) => {
          const { displayUrl, thumbnailUrl, originalName, type } =
            await prepareFileForDisplay(fileInfo, true);
          return {
            file_id: fileInfo.file_id,
            url: displayUrl,
            type,
            thumbnail: thumbnailUrl,
            isUploading: false,
            uploadProgress: 100,
            originalName: originalName,
            metadata: fileInfo.metadata || {},
          };
        })
      );

      messages.push({
        type: "user",
        prompt: conv.prompt || "",
        files,
        status: "completed",
        timestamp: conv.timestamp,
      });
    } else if (conv.role === "assistant") {
      const filesToProcessForAssistant = conv.output_files || [];
      const output_files = await Promise.all(
        filesToProcessForAssistant.map(async (fileInfo) => {
          const { displayUrl, thumbnailUrl, originalName, type } =
            await prepareFileForDisplay(fileInfo, true);
          return {
            file_id: fileInfo.file_id,
            name: fileInfo.original_filename,
            path: fileInfo.s3_key, // CHANGED: Was fileInfo.filename. For deletion, s3_key is used.
            type,
            url: displayUrl,
            thumbnail: thumbnailUrl,
            originalName: originalName,
            metadata: fileInfo.metadata || {},
          };
        })
      );

      messages.push({
        type: "assistant",
        prompt: "",
        status: "completed",
        response: conv.response,
        output_files,
        timestamp: conv.timestamp,
      });
    }
  }

  return messages;
}

/**
 * Modal Functionality
 */
let currentModalFiles = [];
let currentModalIndex = 0;

function createModalElement() {
  const modal = createElement("div", {
    id: "fileModal",
    className: "modal hidden", // Add hidden class here
    onClick: closeModal,
  });

  const modalContent = createElement("div", {
    className: "modal-content", // Use your new modal-content class
    onClick: (e) => e.stopPropagation(),
  });

  modalContent.appendChild(
    createElement("button", {
      className: "modal-close", // Use your new modal-close class
      innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" class="icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
      </svg>`,
      onclick: closeModal,
    })
  );

  const mediaContainer = createElement("div", {
    id: "modalMediaContainer",
    className: "modal-media-container", // New CSS class
    onclick: (e) => {
      if (e.target.tagName === "VIDEO") {
        e.target.paused ? e.target.play() : e.target.pause();
      }
    },
  });
  modalContent.appendChild(mediaContainer);

  const navContainer = createElement("div", {
    className: "modal-nav-container", // New CSS class
  });

  navContainer.appendChild(
    createElement("button", {
      id: "modalPrevButton",
      className: "modal-nav-button", // New CSS class
      innerHTML: "&larr; Prev",
      onclick: showPreviousFile,
    })
  );

  navContainer.appendChild(
    createElement("span", {
      id: "modalFileInfo",
      className: "modal-file-info", // New CSS class
    })
  );

  navContainer.appendChild(
    createElement("button", {
      id: "modalNextButton",
      className: "modal-nav-button", // New CSS class
      innerHTML: "Next &rarr;",
      onclick: showNextFile,
    })
  );

  modalContent.appendChild(navContainer);

  modalContent.appendChild(
    createElement("button", {
      id: "modalDownloadButton",
      className: "modal-download-button", // New CSS class
      textContent: "Download",
      onclick: downloadCurrentFile,
    })
  );

  modal.appendChild(modalContent);
  return modal;
}

function openFileModal(files, startIndex) {
  if (!files || files.length === 0) return;

  currentModalFiles = files;
  currentModalIndex = startIndex;

  let modal = getElement("fileModal");
  if (!modal) {
    modal = createModalElement();
    document.body.appendChild(modal);
  }

  document.addEventListener("keydown", handleModalKeydown);
  renderModalContent();
  modal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeModal() {
  const modal = getElement("fileModal");
  if (modal) {
    modal.classList.add("hidden");
    const mediaContainer = getElement("modalMediaContainer");
    if (mediaContainer) {
      mediaContainer.innerHTML = "";
    }
    document.body.style.overflow = "auto";
    document.removeEventListener("keydown", handleModalKeydown);
  }
  currentModalFiles = [];
  currentModalIndex = 0;
}

async function renderModalContent() {
  if (currentModalFiles.length === 0) return;

  const file = currentModalFiles[currentModalIndex];
  const mediaContainer = getElement("modalMediaContainer");
  const prevButton = getElement("modalPrevButton");
  const nextButton = getElement("modalNextButton");
  const fileInfoSpan = getElement("modalFileInfo");

  if (!mediaContainer || !prevButton || !nextButton || !fileInfoSpan) return;

  mediaContainer.innerHTML = "";

  const {
    displayUrl,
    thumbnailUrl,
    originalName: modalOriginalName,
    type: modalFileType,
  } = await prepareFileForDisplay(file, false);

  if (modalFileType === "image") {
    mediaContainer.appendChild(
      createElement("img", {
        src: displayUrl,
        alt: modalOriginalName || "Image",
        className: "modal-image",
      })
    );
  } else if (modalFileType === "video") {
    const videoElement = createElement("video", {
      src: displayUrl,
      controls: true,
      className: "modal-video",
      poster: thumbnailUrl || "",
      autoplay: true, // Add autoplay
    });
    // Ensure video plays when loaded
    videoElement.addEventListener("loadeddata", () => {
      videoElement.play().catch((err) => {
        console.warn("Autoplay failed:", err);
      });
    });
    mediaContainer.appendChild(videoElement);
  } else if (modalFileType === "audio") {
    const audioElement = createElement("audio", {
      src: displayUrl,
      controls: true,
      className: "modal-audio",
      autoplay: true, // Add autoplay
    });
    // Ensure audio plays when loaded
    audioElement.addEventListener("loadeddata", () => {
      audioElement.play().catch((err) => {
        console.warn("Autoplay failed:", err);
      });
    });
    mediaContainer.appendChild(audioElement);
  } else {
    mediaContainer.textContent = "Cannot preview this file type.";
  }

  prevButton.disabled = currentModalIndex === 0;
  nextButton.disabled = currentModalIndex === currentModalFiles.length - 1;
  fileInfoSpan.textContent = `${modalOriginalName} (${
    currentModalIndex + 1
  } / ${currentModalFiles.length})`;
}

function showPreviousFile() {
  if (currentModalIndex > 0) {
    currentModalIndex--;
    renderModalContent();
  }
}

function showNextFile() {
  if (currentModalIndex < currentModalFiles.length - 1) {
    currentModalIndex++;
    renderModalContent();
  }
}

function handleModalKeydown(event) {
  const modal = getElement("fileModal");
  if (!modal || modal.classList.contains("hidden")) return;

  if (event.key === "Escape") {
    closeModal();
  } else if (event.key === "ArrowLeft") {
    showPreviousFile();
  } else if (event.key === "ArrowRight") {
    showNextFile();
  }
}

async function downloadCurrentFile() {
  if (currentModalFiles.length === 0) return;

  const file = currentModalFiles[currentModalIndex];
  const fileUrlResponse = await api.getFileUrl(file.file_id, false, true);
  const displayUrl = fileUrlResponse?.url;
  const downloadOriginalName =
    fileUrlResponse?.filename || file.originalName || "download";

  if (!displayUrl) {
    showError("Could not get file URL for download. Please try again.");
    return;
  }

  const link = document.createElement("a");
  link.href = displayUrl;
  link.download = downloadOriginalName;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * UI Utility Functions
 */
function showElement(id) {
  const element = getElement(id);
  if (element) element.classList.remove("hidden");
}

function hideElement(id) {
  const element = getElement(id);
  if (element) element.classList.add("hidden");
}

function showWelcomeMessage() {
  showElement("welcomeMessage");
}

function hideWelcomeMessage() {
  hideElement("welcomeMessage");
}

function showError(message) {
  const errorContainer = getElement("errorContainer");
  if (!errorContainer) return;

  errorContainer.textContent = message;
  errorContainer.classList.remove("hidden");
}

function hideError() {
  const errorContainer = getElement("errorContainer");
  if (!errorContainer) return;

  errorContainer.classList.add("hidden");
  errorContainer.textContent = "";
}

function toggleSidebar() {
  const sidebar = getElement("sidebar");
  if (!sidebar) return;

  state.ui.isSidebarOpen = !state.ui.isSidebarOpen;
  sidebar.classList.toggle("open", state.ui.isSidebarOpen);
}

function checkScreenSize() {
  state.ui.isMobile = window.innerWidth < 768;
}

// --- MODIFICATION START: updateUrl ---
function updateUrl(chatId) {
  const newPath = chatId ? `/c/${chatId}` : "/"; // <<< CHANGED HERE (was /c)
  if (window.location.pathname !== newPath || window.location.search !== "") {
    // Ensure search is also cleared for base path
    history.pushState({ chatId }, "", newPath);
  }
  state.chat.active = chatId;
}
// --- MODIFICATION END: updateUrl ---

async function renderFileGrid(files, options = {}) {
  const { isProcessing = false, dimmed = false, onFileClick } = options;

  const filesDiv = createElement("div", {
    className: `file-grid ${dimmed ? "dimmed" : ""}`, // New CSS classes
  });

  if (!files || files.length === 0) {
    return filesDiv;
  }

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const filePreviewData = { ...file };

    filesDiv.appendChild(
      createFilePreviewElement(filePreviewData, {
        isProcessing,
        isDownloadable: false,
        isRemovable: false,
        onClick: (e) => {
          e.stopPropagation();
          if (onFileClick) {
            onFileClick(files, i);
          }
        },
      })
    );
  }

  return filesDiv;
}

/**
 * Chat Management Functions
 */
async function loadAllChats() {
  try {
    state.ui.isLoading = true;
    hideError();

    const response = await api.getUserChats();

    if (response && Array.isArray(response)) {
      state.chat.all = {};

      response.forEach((chatSummary) => {
        if (chatSummary.chat_id) {
          state.chat.all[chatSummary.chat_id] = {
            name: chatSummary.chat_name,
            messages: [],
            created_at: chatSummary.created_at,
            updated_at: chatSummary.updated_at,
          };
        }
      });

      renderChatHistory();
    }
  } catch (err) {
    console.error("Failed to load all chats:", err);
    showError("Failed to load chat history");
  } finally {
    state.ui.isLoading = false;
  }
}

async function loadChat(chatId) {
  try {
    state.ui.isLoading = true;
    hideError();

    const response = await api.getChat(chatId);

    if (response) {
      const chatMessages = await transformConversationsToMessages(
        response.conversations || []
      );

      if (!state.chat.all[chatId]) {
        state.chat.all[chatId] = {};
      }

      state.chat.all[chatId].messages = chatMessages;
      state.chat.all[chatId].name = response.chat_name;
      state.chat.messages = chatMessages;
      state.chat.active = chatId;
      state.chat.current = chatId;

      renderMessages();
      updateUrl(chatId);
      renderChatHistory();

      setTimeout(() => scrollToBottom(), 100);
    }
  } catch (err) {
    console.error("Error loading chat:", err);

    if (err.message?.includes("404")) {
      delete state.chat.all[chatId];

      if (state.chat.active === chatId) {
        state.chat.active = null;
        state.chat.current = null;
        state.chat.messages = [];
        renderMessages();
        showWelcomeMessage();
        updateUrl(null);
      }

      renderChatHistory();
    } else {
      showError("Failed to load chat");
    }
  } finally {
    state.ui.isLoading = false;
  }
}

async function createNewChat() {
  try {
    hideError();
    const response = await api.createChat("");

    if (response?.chat_id) {
      state.chat.all[response.chat_id] = {
        name: "New Chat",
        messages: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };

      state.chat.current = response.chat_id;
      state.chat.active = response.chat_id;
      state.chat.messages = [];

      updateUrl(response.chat_id);
      if (state.ui.isSidebarOpen && state.ui.isMobile) {
        toggleSidebar();
      }

      const promptInput = getElement("promptInput");
      if (promptInput) {
        promptInput.value = "";
        promptInput.focus();
      }

      renderMessages();
      renderChatHistory();
    }
  } catch (err) {
    console.error("Failed to create chat:", err);
    showError("Failed to create new chat");
  }
}

async function switchChat(chatId) {
  try {
    hideError();
    state.chat.active = chatId;
    await loadChat(chatId);

    if (state.ui.isSidebarOpen && state.chat.active === chatId) {
      toggleSidebar();
    }

    focusInput(); // Auto-focus the input when switching chats
  } catch (err) {
    console.error("Error switching chat:", err);
    showError("Failed to switch chat");
  }
}

async function deleteChat(chatId, event, buttonToDisable = null) {
  event.stopPropagation(); // Prevent chat from being selected

  const chatItem = document.getElementById(`chat-item-${chatId}`);
  if (!chatItem) return;

  // Use the passed button if available, otherwise query for it.
  const deleteButton =
    buttonToDisable ||
    chatItem.querySelector(".delete-confirm-button") ||
    chatItem.querySelector(".chat-delete-button");
  if (!deleteButton) return;

  // Store original content and disable button
  const originalContent = deleteButton.innerHTML;
  deleteButton.disabled = true;
  deleteButton.innerHTML = '<div class="delete-spinner"></div>'; // Use custom spinner class

  try {
    hideError();
    await api.deleteChat(chatId);

    // Remove chat from local state
    delete state.chat.all[chatId];

    // If the deleted chat was active, reset the view
    if (state.chat.active === chatId) {
      state.chat.active = null;
      state.chat.current = null;
      state.chat.messages = [];
      renderMessages(); // Clear messages view
      showWelcomeMessage(); // Show welcome/empty state
      updateUrl(null); // Clear chat_id from URL
      focusInput(); // Focus input after deleting active chat
    }

    // Re-render chat history to remove the item
    renderChatHistory();
  } catch (err) {
    console.error("Failed to delete chat:", err);
    showError("Failed to delete chat. Please try again.");
    // Restore original content if deletion failed
    deleteButton.disabled = false;
    deleteButton.innerHTML = originalContent;
    // If it was a confirm button, remove the confirmation UI
    const confirmContainer = chatItem.querySelector(
      ".delete-confirm-container"
    );
    if (confirmContainer) {
      confirmContainer.remove();
    }
  }
}

function addOptimisticMessages(chatId, userMessage, processingMessage) {
  if (!chatId) return;

  if (!state.chat.all[chatId]) {
    state.chat.all[chatId] = { name: null, messages: [] };
  }

  if (!Array.isArray(state.chat.all[chatId].messages)) {
    state.chat.all[chatId].messages = [];
  }

  state.chat.all[chatId].messages = [
    ...state.chat.all[chatId].messages,
    userMessage,
    processingMessage,
  ];

  state.chat.messages = state.chat.all[chatId].messages;

  renderMessages();
  scrollToBottom();
  hideWelcomeMessage();
}

function removeProcessingMessage(chatId) {
  if (
    !chatId ||
    !state.chat.all[chatId] ||
    !Array.isArray(state.chat.all[chatId].messages)
  ) {
    return;
  }

  state.chat.all[chatId].messages = state.chat.all[chatId].messages.filter(
    (msg) => !(msg.type === "assistant" && msg.status === "processing")
  );

  if (state.chat.active === chatId) {
    state.chat.messages = state.chat.all[chatId].messages;
    renderMessages();
  }
}

/**
 * File Management Functions
 */
async function handleFiles(fileList) {
  const allowedImageTypes = [
    "image/png",
    "image/jpeg",
    "image/jpg", // Some browsers use jpg instead of jpeg
    "image/webp",
    "image/heic",
    "image/heif",
    "image/x-png", // Alternative for PNG
    "image/x-jpeg", // Alternative for JPEG
  ];

  const allowedVideoTypes = [
    "video/mp4",
    "video/mpeg",
    "video/mov",
    "video/quicktime", // Alternative for MOV
    "video/avi",
    "video/x-flv",
    "video/mpg",
    "video/mpeg4", // Alternative for MP4
    "video/webm",
    "video/wmv",
    "video/x-ms-wmv", // Alternative for WMV
    "video/3gpp",
    "video/x-msvideo", // Alternative for AVI
  ];

  const allowedAudioTypes = [
    "audio/wav",
    "audio/x-wav", // Alternative for WAV
    "audio/wave", // Alternative for WAV
    "audio/mp3",
    "audio/mpeg", // Alternative for MP3
    "audio/x-mpeg", // Alternative for MP3
    "audio/mpeg3", // Alternative for MP3
    "audio/aiff",
    "audio/x-aiff", // Alternative for AIFF
    "audio/aac",
    "audio/x-aac", // Alternative for AAC
    "audio/ogg",
    "audio/x-ogg", // Alternative for OGG
    "audio/flac",
    "audio/x-flac", // Alternative for FLAC
  ];

  const allowedDocumentTypes = [
    "application/pdf",
    "application/x-pdf", // Alternative for PDF
    "application/x-javascript",
    "text/javascript",
    "application/x-python",
    "text/x-python",
    "text/plain",
    "text/html",
    "text/css",
    "text/markdown", // Alternative for MD
    "text/md",
    "text/csv",
    "text/xml",
    "text/rtf",
    "application/rtf", // Alternative for RTF
  ];

  const allowedTypes = [
    ...allowedImageTypes,
    ...allowedVideoTypes,
    ...allowedAudioTypes,
    ...allowedDocumentTypes,
  ];

  // Debug logging
  console.log(
    "File types being processed:",
    Array.from(fileList).map((file) => ({
      name: file.name,
      type: file.type,
    }))
  );

  const newFiles = Array.from(fileList).filter((file) => {
    const isAllowed = allowedTypes.includes(file.type);
    if (!isAllowed) {
      console.log(`File ${file.name} with type ${file.type} was rejected`);
    }
    return isAllowed;
  });

  if (newFiles.length === 0) {
    showError(
      "Please select valid files. Allowed formats:\n" +
        "Images: PNG, JPEG, WEBP, HEIC, HEIF\n" +
        "Videos: MP4, MPEG, MOV, AVI, FLV, MPG, WEBM, WMV, 3GPP\n" +
        "Audio: WAV, MP3, AIFF, AAC, OGG, FLAC\n" +
        "Documents: PDF, JavaScript, Python, TXT, HTML, CSS, Markdown, CSV, XML, RTF"
    );
    return;
  }

  hideError();
  updateSendButtonState(); // Disable button immediately when files are selected

  const newFilePreviews = [];

  for (const file of newFiles) {
    const type = file.type.startsWith("image/")
      ? "image"
      : file.type.startsWith("video/")
      ? "video"
      : "audio";

    // Create a new preview object for the state
    const preview = {
      type,
      isUploading: true, // Starts as true, uploadFile callback will update progress
      isLoadingThumbnail: true, // Assumed true until poller confirms or S3 upload response provides it
      uploadProgress: 0, // Will be 0 or 100 in batch mode without per-file progress
      originalName: file.name,
      thumbnail:
        type === "video"
          ? "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='320' height='240' viewBox='0 0 320 240'%3E%3Crect width='320' height='240' fill='%23e5e7eb'/%3E%3Cpath d='M144 96v48l32-24-32-24z' fill='%239ca3af'/%3E%3C/svg%3E"
          : undefined,
      file_id: null, // Will be populated after upload record creation
      filePath: null, // Will be S3 key, populated after upload
    };
    newFilePreviews.push(preview);
  }

  // Add all new previews to state at once to minimize re-renders if renderFilePreviews is called inside loop
  state.files.previews.push(...newFilePreviews);
  renderFilePreviews(); // Render once after adding all new previews

  // Get references to the preview objects we just added to the state
  // These are the previews that this batch upload operation is responsible for updating.
  const justAddedPreviews = state.files.previews.slice(-newFiles.length);

  if (newFiles.length > 0) {
    api
      .uploadFiles(
        newFiles,
        state.chat.current || undefined /* No progress callback for batch */
      )
      .then((batchUploadResponse) => {
        if (
          batchUploadResponse &&
          batchUploadResponse.files &&
          Array.isArray(batchUploadResponse.files)
        ) {
          // Check if the number of files in response matches the number of files sent
          if (batchUploadResponse.files.length !== newFiles.length) {
            console.warn(
              "Batch upload response file count mismatch. Sent:",
              newFiles.length,
              "Received:",
              batchUploadResponse.files.length
            );
            // Handle this discrepancy - perhaps mark all related previews as failed or show a general error.
            // For now, we will try to match what we can.
          }

          batchUploadResponse.files.forEach((uploadedFileData) => {
            const previewInState = justAddedPreviews.find(
              (p) =>
                p.originalName === uploadedFileData.original_filename &&
                p.isUploading &&
                !p.file_id
            );
            if (previewInState) {
              previewInState.file_id = uploadedFileData.file_id;
              previewInState.filePath = uploadedFileData.s3_key;
              previewInState.isUploading = false;
              previewInState.uploadProgress = 100;

              if (previewInState.type === "image" && previewInState.file_id) {
                pollForImageThumbnail(previewInState.file_id);
              } else if (
                previewInState.type === "video" &&
                previewInState.file_id
              ) {
                pollForVideoThumbnail(previewInState.file_id);
              } else if (previewInState.type === "audio") {
                previewInState.isLoadingThumbnail = false;
              }
            } else {
              console.warn(
                "Could not find matching preview in state for uploaded file:",
                uploadedFileData.original_filename
              );
            }
          });
        } else {
          throw new Error(
            "Batch upload response did not contain expected files array or was malformed."
          );
        }

        if (batchUploadResponse.chat_id && !state.chat.current) {
          state.chat.current = batchUploadResponse.chat_id;
          state.chat.active = batchUploadResponse.chat_id;
          state.chat.all[batchUploadResponse.chat_id] = state.chat.all[
            batchUploadResponse.chat_id
          ] || { name: "New Chat", messages: [] };
          updateUrl(batchUploadResponse.chat_id);
          renderChatHistory();
        }
      })
      .catch((err) => {
        console.error("Failed to upload batch of files:", err);
        showError(
          `Failed to upload files. ${err.message || "Please try again."}`
        );
        // Remove previews that were part of this failed batch and are still marked as uploading
        justAddedPreviews.forEach((preview) => {
          if (preview.isUploading) {
            // If it's still marked as uploading, its processing failed.
            state.files.previews = state.files.previews.filter(
              (pState) => pState !== preview
            );
          }
        });
      })
      .finally(() => {
        // Ensure any previews that didn't get a file_id (e.g. due to mismatch or partial failure)
        // are no longer marked as 'isUploading' to prevent them from blocking the send button indefinitely.
        justAddedPreviews.forEach((p) => {
          if (p.isUploading && !p.file_id) {
            p.isUploading = false; // Mark as not uploading, but it won't have a file_id
            // Consider adding an error state to the preview object itself.
          }
        });
        renderFilePreviews();
        updateSendButtonState();
      });
  }
}

/**
 * Render Functions
 */
function renderChatHistory() {
  const chatHistory = getElement("chatHistory");
  if (!chatHistory) return;

  chatHistory.innerHTML = "";

  const sortedChats = Object.entries(state.chat.all)
    .map(([id, chatData]) => ({
      id,
      name: chatData.name || "Untitled Chat",
      updated_at:
        chatData.updated_at || chatData.created_at || new Date(0).toISOString(),
    }))
    .sort((a, b) => getSortTimestamp(b) - getSortTimestamp(a));

  if (sortedChats.length === 0) {
    return;
  }

  sortedChats.forEach((chat) => {
    const isActive = chat.id === state.chat.active;
    const chatName = chat.name || "Untitled Chat";

    const item = createElement("li", {
      id: `chat-item-${chat.id}`,
      className: "chat-history-item",
    });

    const link = createElement("a", {
      href: "#",
      className: `chat-history-link ${isActive ? "active" : ""}`,
      onClick: (e) => {
        e.preventDefault();
        if (state.chat.active !== chat.id) {
          switchChat(chat.id);
        } else if (state.ui.isMobile) {
          toggleSidebar();
        }
      },
    });

    const nameSpan = createElement("span", {
      className: "chat-name",
      textContent: chatName,
    });

    const actionsContainer = createElement("div", {
      className: "chat-actions-container",
    });

    const threeDotsButton = createElement("button", {
      className: "chat-options-button",
      title: "Chat options",
      innerHTML: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="icon-sm"><path d="M10 6a2 2 0 110-4 2 2 0 010 4zM10 12a2 2 0 110-4 2 2 0 010 4zM10 18a2 2 0 110-4 2 2 0 010 4z" /></svg>`,
      onClick: (event) => {
        event.stopPropagation();
        event.preventDefault();
        const existingConfirm = item.querySelector(".delete-confirm-container");
        if (existingConfirm) {
          existingConfirm.remove();
        } else {
          const deleteConfirmContainer = createElement("div", {
            className: "delete-confirm-container",
          });
          const confirmButton = createElement("button", {
            className: "delete-confirm-button",
            textContent: "Delete",
            onClick: (e) => {
              e.stopPropagation();
              e.preventDefault();
              deleteChat(chat.id, e, confirmButton); // Pass button to disable
            },
          });
          deleteConfirmContainer.appendChild(confirmButton);
          actionsContainer.appendChild(deleteConfirmContainer); // Append to actions container
        }
      },
    });

    actionsContainer.appendChild(threeDotsButton);
    link.appendChild(nameSpan);
    link.appendChild(actionsContainer); // Changed from deleteContainer
    item.appendChild(link);
    chatHistory.appendChild(item);
  });
}

function updateInputPosition() {
  const inputArea = getElement("inputArea");
  inputArea.classList.remove("centered");
  inputArea.classList.add("bottom");
}

async function renderMessages() {
  const messagesDiv = getElement("messages");
  if (!messagesDiv) return;

  const messagesToRender = state.chat.messages || [];
  updateInputPosition();

  Object.keys(state.activeTimers).forEach((timestamp) => {
    const messageExists = messagesToRender.some(
      (msg) =>
        msg.timestamp === timestamp &&
        (msg.status === "processing" ||
          (state.activeTimers[timestamp] &&
            state.activeTimers[timestamp].isTaskPollingTimer))
    );
    if (
      !messageExists &&
      state.activeTimers[timestamp] &&
      !state.activeTimers[timestamp].isTaskPollingTimer
    ) {
      clearInterval(state.activeTimers[timestamp].intervalId);
      delete state.activeTimers[timestamp];
    }
  });

  if (!state.chat.active || messagesToRender.length === 0) {
    messagesDiv.innerHTML = "";
    // Show welcome message when there's an active chat with no messages
    if (state.chat.active && messagesToRender.length === 0) {
      showWelcomeMessage();
    }
    return;
  }

  hideWelcomeMessage();
  messagesDiv.innerHTML = "";
  const fragment = document.createDocumentFragment();

  for (const message of messagesToRender) {
    const messageDiv = createElement("div", {
      className: `message ${
        message.type === "user" ? "user-message" : "assistant-message"
      }`,
    });

    const contentDiv = createElement("div", {
      className: `message-content ${
        message.type === "user" ? "user-content" : "assistant-content"
      }`,
    });

    if (message.type === "user") {
      if (message.files?.length > 0) {
        message.files.forEach((file) => {
          if (
            file.file_id &&
            !isCurrentlyPollingGlobalThumbnail(file.file_id)
          ) {
            if (
              file.type === "video" &&
              isPlaceholderThumbnail(file.thumbnail)
            ) {
              startGlobalThumbnailPolling(file.file_id);
            } else if (
              file.type === "image" &&
              (!file.thumbnail || file.thumbnail === file.url)
            ) {
              startGlobalThumbnailPolling(file.file_id);
            }
          }
        });
        contentDiv.appendChild(
          await renderFileGrid(message.files, {
            onFileClick: openFileModal,
          })
        );
      }

      if (message.prompt) {
        contentDiv.appendChild(
          createElement("p", {
            className: "message-text",
            textContent: message.prompt,
          })
        );
      }
    } else if (message.type === "assistant") {
      if (message.status === "processing") {
        const processingContainer = createElement("div", {
          className: "processing-container",
        });

        if (message.output_files?.length > 0) {
          message.output_files.forEach((file) => {
            if (
              file.file_id &&
              !isCurrentlyPollingGlobalThumbnail(file.file_id)
            ) {
              if (
                file.type === "video" &&
                isPlaceholderThumbnail(file.thumbnail)
              ) {
                startGlobalThumbnailPolling(file.file_id);
              } else if (
                file.type === "image" &&
                (!file.thumbnail || file.thumbnail === file.url)
              ) {
                startGlobalThumbnailPolling(file.file_id);
              }
            }
          });
          processingContainer.appendChild(
            renderFileGrid(message.output_files, {
              isProcessing: true,
              dimmed: true,
              onFileClick: openFileModal,
            })
          );
        }

        const loadingDiv = createElement("div", {
          className: "loading-indicator",
        });

        loadingDiv.appendChild(
          createElement("div", {
            className: "spinner",
          })
        );

        const detailTextSpanId = `detailText-${
          message.timestamp || Date.now()
        }`;
        const timerSpanId = `timerText-${message.timestamp || Date.now()}`;

        loadingDiv.appendChild(
          createElement("span", {
            id: detailTextSpanId,
            className: "loading-text",
            textContent: message.response?.message || "Processing...",
          })
        );
        loadingDiv.appendChild(
          createElement("span", {
            id: timerSpanId,
            className: "loading-timer",
            textContent: "(0s)",
          })
        );

        processingContainer.appendChild(loadingDiv);
        contentDiv.appendChild(processingContainer);

        const messageTimestamp = message.timestamp || Date.now();
        if (
          !state.activeTimers[messageTimestamp] &&
          message.status === "processing"
        ) {
          const startTime = Date.now();
          state.activeTimers[messageTimestamp] = {
            intervalId: setInterval(() => {
              const elapsedSeconds = Math.floor(
                (Date.now() - startTime) / 1000
              );
              const timerElement = getElement(timerSpanId);
              if (timerElement) {
                timerElement.textContent = `(${elapsedSeconds}s)`;
              } else {
                clearInterval(state.activeTimers[messageTimestamp].intervalId);
                delete state.activeTimers[messageTimestamp];
              }
            }, 1000),
            isTaskPollingTimer: false,
          };
        }
      } else {
        const uiTimerKey = message.timestamp;
        if (
          uiTimerKey &&
          state.activeTimers[uiTimerKey] &&
          !state.activeTimers[uiTimerKey].isTaskPollingTimer
        ) {
          clearInterval(state.activeTimers[uiTimerKey].intervalId);
          delete state.activeTimers[uiTimerKey];
        }

        if (message.response?.message) {
          const responseText = createElement("p", {
            className: "message-text",
            textContent: message.response.message,
          });
          contentDiv.appendChild(responseText);
        }

        if (message.output_files?.length > 0) {
          message.output_files.forEach((file) => {
            if (
              file.file_id &&
              !isCurrentlyPollingGlobalThumbnail(file.file_id)
            ) {
              if (
                file.type === "video" &&
                isPlaceholderThumbnail(file.thumbnail)
              ) {
                startGlobalThumbnailPolling(file.file_id);
              } else if (
                file.type === "image" &&
                (!file.thumbnail || file.thumbnail === file.url)
              ) {
                startGlobalThumbnailPolling(file.file_id);
              }
            }
          });
          contentDiv.appendChild(
            await renderFileGrid(message.output_files, {
              onFileClick: openFileModal,
            })
          );
        }

        if (message.status === "failed") {
          contentDiv.appendChild(
            createElement("p", {
              className: "error-text",
              textContent: "Processing failed.",
            })
          );
        }
      }
    }

    messageDiv.appendChild(contentDiv);
    fragment.appendChild(messageDiv);
  }

  messagesDiv.appendChild(fragment);
  scrollToBottom();
}

function renderFilePreviews() {
  const filePreviewsContainer = getElement("filePreviews"); // Corrected ID from HTML "file-previews-container" to "filePreviews"
  if (!filePreviewsContainer) {
    // console.warn("filePreviews container not found, cannot render previews.");
    return;
  }

  filePreviewsContainer.innerHTML = ""; // Clear existing previews

  // Show or hide the container based on whether there are files
  if (state.files.previews.length === 0) {
    filePreviewsContainer.classList.add("hidden");
  } else {
    filePreviewsContainer.classList.remove("hidden");
  }

  // updateInputPosition(); // Called from renderMessages, ensure it's called if previews change layout significantly

  const fragment = document.createDocumentFragment();

  state.files.previews.forEach((file) => {
    // file object from state.files.previews
    if (!file) return;

    const previewElement = createFilePreviewElement(file, {
      isRemovable: true,
      isProcessing: file.isUploading,
      isLoadingThumbnail: file.isLoadingThumbnail,
      onRemove: async (fileClicked) => {
        // This 'fileClicked' is passed from createFilePreviewElement's onclick
        // This function now handles ONLY poller clearing and backend deletion.
        // UI removal is already done by the click handler in createFilePreviewElement.

        // 1. Clear Pollers for 'fileClicked'
        if (fileClicked.file_id) {
          const pollerKeyPrefix =
            fileClicked.type === "video"
              ? "videoThumbPoll-"
              : "imageThumbPoll-";
          const pollerKey = `${pollerKeyPrefix}${fileClicked.file_id}`;
          if (
            state.activeInputThumbnailPollers &&
            state.activeInputThumbnailPollers[pollerKey]
          ) {
            clearInterval(state.activeInputThumbnailPollers[pollerKey]);
            delete state.activeInputThumbnailPollers[pollerKey];
          }
        }

        // 2. Perform Backend Deletion for 'fileClicked'
        // Ensure filePath (S3 key) and file_id exist, indicating it's a server-side entity.
        if (fileClicked.filePath && fileClicked.file_id) {
          try {
            await api.deleteFile(fileClicked.filePath);
          } catch (err) {
            console.error(
              `Error deleting file ${fileClicked.originalName} (Path: ${fileClicked.filePath}) from server:`,
              err
            );
            showError(
              `Error deleting ${fileClicked.originalName} from server.`
            );
            // Consider if the file should be re-added to UI if backend deletion fails.
            // For now, it remains visually removed.
          }
        } else if (fileClicked.isUploading) {
          console.warn(
            `File ${fileClicked.originalName} was removed while still uploading (no filePath/file_id). No backend deletion attempted.`
          );
        } else {
          console.warn(
            `File ${fileClicked.originalName} missing filePath or file_id. No backend deletion attempted. file_id: ${fileClicked.file_id}, filePath: ${fileClicked.filePath}`
          );
        }
        // No call to renderFilePreviews() or state modification here.
        // updateSendButtonState() will be called by renderFilePreviews in the click handler.
      },
      onClick: () => {
        // From original renderFilePreviews, for opening modal
        if (file.thumbnail && !file.isUploading && !file.isLoadingThumbnail) {
          // openModal(file.thumbnail, file.type, file.originalName); // Needs to be adapted for modal files structure
        } else if (!file.isUploading && !file.isLoadingThumbnail) {
          // ...
        }
      },
    });
    fragment.appendChild(previewElement);
  });

  filePreviewsContainer.appendChild(fragment);
  updateSendButtonState(); // Ensure button state is correct after rendering
  updateInputPosition(); // Update input position based on presence of previews
}

function updateToggleButton() {
  const themeToggleText = getElement("themeToggleText");
  if (!themeToggleText) return;

  const isDark = document.body.classList.contains("dark");
  themeToggleText.textContent = isDark ? "Light Mode" : "Dark Mode";
}

function scrollToBottom() {
  const mainContent = getElement("mainContent");
  if (mainContent) {
    requestAnimationFrame(() => {
      mainContent.scrollTop = mainContent.scrollHeight;
      setTimeout(() => {
        mainContent.scrollTop = mainContent.scrollHeight;
      }, 100); // Added slight delay for very fast renders
    });
  }
}

/**
 * Form Submission Handler
 */
async function updateUserCredits() {
  try {
    const userInfo = await api.getUserInfo();
    if (userInfo) {
      state.user.plan = userInfo.plan || "free";
      state.user.credits = userInfo.credits || 0;
      state.user.email = userInfo.email || "";
      state.user.user_id = userInfo.user_id || null;

      const planName = state.user.plan || "free";
      const formattedPlanName =
        planName.charAt(0).toUpperCase() + planName.slice(1);

      const userPlanCredits = getElement("userPlanCredits");
      const creditsText = `${formattedPlanName} (${state.user.credits} credits)`;

      if (userPlanCredits) {
        userPlanCredits.textContent = creditsText;
      }

      const userDropdownEmailElement = getElement("userDropdownEmail");
      if (userDropdownEmailElement) {
        userDropdownEmailElement.textContent = state.user.email;
        userDropdownEmailElement.title = state.user.email;
      }

      const upgradeBtn = getElement("upgradeBtn");
      const manageSubBtn = getElement("manageSubscriptionBtn");
      if (upgradeBtn && manageSubBtn) {
        if (state.user.plan === "free") {
          upgradeBtn.classList.remove("hidden");
          manageSubBtn.classList.add("hidden");
        } else {
          upgradeBtn.classList.add("hidden");
          manageSubBtn.classList.remove("hidden");
        }
      }
    }
  } catch (err) {
    console.error("Failed to update user credits:", err);
  }
}

async function handleSubmit(e) {
  e.preventDefault();

  const promptInput = getElement("promptInput");
  const prompt = promptInput?.value.trim() || "";
  const previewsFromState = [...state.files.previews];

  if (!prompt && previewsFromState.length === 0) {
    return;
  }

  const allFilesHaveFileId = previewsFromState.every((p) => p.file_id);
  if (!allFilesHaveFileId) {
    showError(
      "Some files are still processing or encountered an issue. Please wait or remove them before sending."
    );
    console.warn(
      "[handleSubmit] Not all files have file_id. Aborting submission."
    );
    updateSendButtonState(); // Ensure button is disabled if files aren't ready
    return;
  }

  hideError();
  state.ui.isLoading = true;
  updateSendButtonState(); // Disable button when starting submission

  const fileInfoForProcess = previewsFromState.map((file) => ({
    s3_key: file.filePath,
    file_id: file.file_id,
  }));

  const filesForUserMessageDisplay = await Promise.all(
    previewsFromState.map(async (previewFile) => {
      if (previewFile.file_id) {
        const prepared = await prepareFileForDisplay(previewFile, true);
        return {
          ...previewFile,
          url: prepared.displayUrl,
          thumbnail: prepared.thumbnailUrl,
          isUploading: false,
          uploadProgress: 100,
        };
      }
      return { ...previewFile, isUploading: false, uploadProgress: 100 };
    })
  );

  const userMessageForDisplay = {
    type: "user",
    prompt,
    files: filesForUserMessageDisplay,
    status: "completed",
    timestamp: new Date().toISOString(),
  };

  const processingMessageTimestamp =
    userMessageForDisplay.timestamp + "_processing";
  const processingMessage = {
    type: "assistant",
    prompt: "",
    response: { message: "Initiating..." },
    status: "processing",
    timestamp: processingMessageTimestamp,
    output_files: [],
  };

  if (promptInput) promptInput.value = "";
  state.files.previews = [];
  state.files.list = [];
  renderFilePreviews();

  previewsFromState.forEach((preview) => {
    if (preview.url?.startsWith("blob:")) URL.revokeObjectURL(preview.url);
    if (preview.thumbnail?.startsWith("blob:"))
      URL.revokeObjectURL(preview.thumbnail);
  });

  let currentActiveChatId = state.chat.active;

  try {
    if (currentActiveChatId) {
      const commitResponse = await api.createChat(
        prompt,
        [], // Pass an empty array for filePaths
        currentActiveChatId
      );

      if (!commitResponse || commitResponse.chat_id !== currentActiveChatId) {
        console.error(
          "[handleSubmit] Error committing user turn or chat_id mismatch.",
          commitResponse
        );
        showError("Failed to update chat before processing. Please try again.");
        state.ui.isLoading = false;
        updateSendButtonState(); // Ensure button is enabled if submission fails
        return;
      }

      // Optimistic UI update for user message happens *after* successful commit
      addOptimisticMessages(
        currentActiveChatId,
        userMessageForDisplay, // This object contains the prompt and resolved files
        processingMessage // The "Assistant is thinking..." message
      );
      scrollToBottom();

      // Step 2: Now call processChat to start the LLM task
      const taskInfo = await api.processChat(currentActiveChatId);

      if (taskInfo && taskInfo.task_id) {
        startTaskPolling(
          taskInfo.task_id,
          currentActiveChatId,
          processingMessageTimestamp
        );
      } else {
        throw new Error("Failed to initiate processing task.");
      }
    } else {
      const createResponse = await api.createChat(prompt); // Pass only prompt here as files are handled by /upload
      if (!createResponse?.chat_id) {
        throw new Error("Failed to create new chat via API.");
      }
      const newChatId = createResponse.chat_id;
      currentActiveChatId = newChatId;

      state.chat.active = newChatId;
      state.chat.current = newChatId;
      state.chat.all[newChatId] = { name: null, messages: [] };
      updateUrl(newChatId);
      renderChatHistory();

      addOptimisticMessages(
        newChatId,
        userMessageForDisplay,
        processingMessage
      );
      scrollToBottom();
      const taskInfo = await api.processChat(newChatId);

      if (taskInfo && taskInfo.task_id) {
        startTaskPolling(
          taskInfo.task_id,
          newChatId,
          processingMessageTimestamp
        );
      } else {
        throw new Error("Failed to initiate processing task for new chat.");
      }
    }
  } catch (err) {
    console.error("Error submitting message:", err);
    if (currentActiveChatId) {
      updateProcessingMessageToFailed(
        currentActiveChatId,
        processingMessageTimestamp,
        err.message || "Submission failed"
      );
    }

    if (err.message?.includes("503") && err.message?.includes("overloaded")) {
      showError(
        "The AI service is currently busy. Please wait a moment and try again."
      );
    } else {
      showError(`Failed to submit message: ${err.message || "Unknown error"}`);
    }
  } finally {
    state.ui.isLoading = false;
    updateSendButtonState(); // Ensure button is enabled if submission fails
  }
}

function startTaskPolling(taskId, chatId, processingMessageTimestamp) {
  let pollCount = 0;
  const taskTimerKey = `taskPoll-${taskId}`;

  if (state.activeTimers[taskTimerKey]) {
    clearInterval(state.activeTimers[taskTimerKey].intervalId);
  }

  state.activeTimers[taskTimerKey] = {
    intervalId: setInterval(async () => {
      pollCount++;
      if (pollCount > MAX_POLLING_ATTEMPTS) {
        clearInterval(state.activeTimers[taskTimerKey].intervalId);
        delete state.activeTimers[taskTimerKey];
        updateProcessingMessageToFailed(
          chatId,
          processingMessageTimestamp,
          "Task timed out. Please check back later or try again."
        );
        return;
      }

      try {
        const taskStatus = await api.getTaskStatus(taskId);
        if (!taskStatus) {
          clearInterval(state.activeTimers[taskTimerKey].intervalId);
          delete state.activeTimers[taskTimerKey];
          updateProcessingMessageToFailed(
            chatId,
            processingMessageTimestamp,
            "Task status could not be retrieved. It might have expired."
          );
          return;
        }

        const {
          status,
          assistant_turn,
          error,
          message: statusMessage,
        } = taskStatus;

        updateProcessingMessageContent(
          chatId,
          processingMessageTimestamp,
          statusMessage || status.charAt(0).toUpperCase() + status.slice(1)
        );

        if (status === "completed") {
          clearInterval(state.activeTimers[taskTimerKey].intervalId);
          delete state.activeTimers[taskTimerKey];
          removeProcessingMessage(chatId, processingMessageTimestamp);

          if (assistant_turn) {
            const filesToProcessForDisplay = assistant_turn.output_files || [];
            const processedOutputFilesForDisplay = await Promise.all(
              filesToProcessForDisplay.map(async (fileInfo) => {
                const { displayUrl, thumbnailUrl, originalName, type } =
                  await prepareFileForDisplay(fileInfo, true);
                return {
                  file_id: fileInfo.file_id,
                  name: fileInfo.original_filename,
                  path: fileInfo.s3_key, // CHANGED: Was fileInfo.filename. For deletion, s3_key is used.
                  type,
                  url: displayUrl,
                  thumbnail: thumbnailUrl,
                  originalName: originalName,
                  metadata: fileInfo.metadata || {},
                };
              })
            );

            const assistantMessageForDisplay = {
              type: "assistant",
              status: "completed",
              response: assistant_turn.response,
              output_files: processedOutputFilesForDisplay,
              timestamp: assistant_turn.timestamp || new Date().toISOString(),
            };

            if (
              state.chat.all[chatId] &&
              Array.isArray(state.chat.all[chatId].messages)
            ) {
              state.chat.all[chatId].messages.push(assistantMessageForDisplay);
              if (state.chat.active === chatId) {
                state.chat.messages = [...state.chat.all[chatId].messages];
                renderMessages();
              }
            }

            try {
              const saveResponse = await api.saveAssistantTurn(
                chatId,
                assistant_turn
              );
            } catch (saveError) {
              console.error(
                "Error saving assistant turn after optimistic update:",
                saveError
              );
            }

            await updateUserCredits();
            const updatedChatData = await api.getChat(chatId);
            if (updatedChatData && updatedChatData.chat_name) {
              state.chat.all[chatId].name = updatedChatData.chat_name;
              renderChatHistory();
            }
          } else {
            console.warn(
              "Task completed but no assistant_turn data received.",
              taskStatus
            );
            updateProcessingMessageToFailed(
              chatId,
              processingMessageTimestamp,
              "Task completed with missing data."
            );
          }
        } else if (status === "failed") {
          clearInterval(state.activeTimers[taskTimerKey].intervalId);
          delete state.activeTimers[taskTimerKey];
          const detailedErrorMessage =
            assistant_turn &&
            assistant_turn.response &&
            assistant_turn.response.message
              ? assistant_turn.response.message
              : error || statusMessage || "Processing failed.";

          const isCreditError =
            assistant_turn?.response?.execution_status ===
            "insufficient_credits";

          updateProcessingMessageToFailed(
            chatId,
            processingMessageTimestamp,
            detailedErrorMessage
          );

          if (isCreditError) {
            openUpgradeModal();
          }

          // Save assistant turn even when task fails, so generated code and logs are preserved
          if (assistant_turn) {
            try {
              const saveResponse = await api.saveAssistantTurn(
                chatId,
                assistant_turn
              );
              console.log(
                "Assistant turn saved successfully for failed task:",
                saveResponse
              );
            } catch (saveError) {
              console.error(
                "Error saving assistant turn for failed task:",
                saveError
              );
            }
          }

          await updateUserCredits();
        } else if (status !== "pending" && status !== "processing") {
          clearInterval(state.activeTimers[taskTimerKey].intervalId);
          delete state.activeTimers[taskTimerKey];
          updateProcessingMessageToFailed(
            chatId,
            processingMessageTimestamp,
            `Unknown task status: ${status}`
          );
        }
      } catch (err) {
        console.error(`Error polling task ${taskId}:`, err);
        clearInterval(state.activeTimers[taskTimerKey].intervalId);
        delete state.activeTimers[taskTimerKey];
        updateProcessingMessageToFailed(
          chatId,
          processingMessageTimestamp,
          `Error fetching task status: ${err.message}`
        );
      }
    }, POLLING_INTERVAL),
    isTaskPollingTimer: true,
  };
}

function updateProcessingMessageContent(chatId, messageTimestamp, newContent) {
  if (
    !chatId ||
    !state.chat.all[chatId] ||
    !Array.isArray(state.chat.all[chatId].messages)
  ) {
    return;
  }

  const messages = state.chat.all[chatId].messages;
  const processingMsgIndex = messages.findIndex(
    (msg) =>
      msg.timestamp === messageTimestamp &&
      msg.type === "assistant" &&
      msg.status === "processing"
  );

  if (processingMsgIndex !== -1) {
    // Only update the message content if it has changed
    if (messages[processingMsgIndex].response.message !== newContent) {
      messages[processingMsgIndex].response = {
        ...messages[processingMsgIndex].response,
        message: newContent,
      };

      // Instead of re-rendering the entire messages, just update the specific text element
      if (state.chat.active === chatId) {
        const detailTextSpan = getElement(`detailText-${messageTimestamp}`);
        if (detailTextSpan) {
          detailTextSpan.textContent = newContent;
        }
      }
    }
  }
}

function updateProcessingMessageToFailed(
  chatId,
  messageTimestamp,
  errorMessage
) {
  if (
    !chatId ||
    !state.chat.all[chatId] ||
    !Array.isArray(state.chat.all[chatId].messages)
  ) {
    return;
  }
  const messages = state.chat.all[chatId].messages;
  const processingMsgIndex = messages.findIndex(
    (msg) => msg.timestamp === messageTimestamp && msg.type === "assistant"
  );

  if (processingMsgIndex !== -1) {
    messages[processingMsgIndex].status = "failed";
    messages[processingMsgIndex].response = { message: errorMessage };

    if (state.activeTimers[messageTimestamp]) {
      clearInterval(state.activeTimers[messageTimestamp].intervalId);
      delete state.activeTimers[messageTimestamp];
    }

    if (state.chat.active === chatId) {
      state.chat.messages = [...messages];
      renderMessages();
    }
  } else {
    const failedMessage = {
      type: "assistant",
      status: "failed",
      response: { message: errorMessage },
      timestamp: new Date().toISOString(),
      output_files: [],
    };
    messages.push(failedMessage);
    if (state.chat.active === chatId) {
      state.chat.messages = [...messages];
      renderMessages();
    }
  }
  scrollToBottom();
}

/**
 * Event Handlers
 */
function handleDragOver(e) {
  e.preventDefault();
  state.ui.dragOver = true;
  const promptInput = getElement("promptInput");
  if (promptInput) {
    promptInput.classList.add("drag-over");
  }
}

function handleDragLeave(e) {
  e.preventDefault();
  state.ui.dragOver = false;
  const promptInput = getElement("promptInput");
  if (promptInput) {
    promptInput.classList.remove("drag-over");
  }
}

function handleDrop(e) {
  e.preventDefault();
  e.stopPropagation();
  state.ui.dragOver = false;

  const promptInput = getElement("promptInput");
  if (promptInput) {
    promptInput.classList.remove("drag-over");
  }

  if (e.dataTransfer?.files) {
    handleFiles(e.dataTransfer.files);
  }
}

function handleFileSelect(e) {
  if (e.target.files) {
    handleFiles(e.target.files);
  }
}

// --- MODIFICATION START: handlePopState ---
function handlePopState() {
  const path = window.location.pathname;
  const parts = path.split("/").filter((p) => p); // filter out empty strings from split
  let chatId = null;

  if (path === "/") {
    // Base chat page, chatId remains null
  } else if (parts.length === 2 && parts[0] === "c") {
    // Path is /c/UUID
    chatId = parts[1]; // The UUID is the second part after split and filter
  }

  if (chatId && chatId !== state.chat.active) {
    switchChat(chatId);
  } else if (!chatId && path === "/" && state.chat.active) {
    // Navigated from /c/some-id or other page to /
    state.chat.active = null;
    state.chat.current = null;
    state.chat.messages = [];
    renderChatHistory();
    renderMessages();
    updateSendButtonState();
    focusInput(); // Focus input when navigating back to home page
  } else if (path !== "/" && (!chatId || !path.startsWith("/c/"))) {
    // If the path is not / and not /c/uuid, it might be an invalid state or different page
    // Potentially redirect to / or show an error, or let it be if other parts of app handle it
    // For now, if it's not a recognized chat URL, do nothing here, assuming other logic handles it or it's a non-chat page.
  }
}
// --- MODIFICATION END: handlePopState ---

function initializeTheme() {
  const html = document.documentElement;
  const body = document.body;

  const savedTheme = localStorage.getItem("theme");
  const systemPrefersDark = window.matchMedia(
    "(prefers-color-scheme: dark)"
  ).matches;

  if (savedTheme === "dark" || (!savedTheme && systemPrefersDark)) {
    html.classList.add("dark");
    body.classList.add("dark");
  }

  updateToggleButton();

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", (e) => {
      if (!localStorage.getItem("theme")) {
        html.classList.toggle("dark", e.matches);
        body.classList.toggle("dark", e.matches);
        updateToggleButton();
      }
    });
}

/**
 * Setup Event Listeners
 */
function setupEventListeners() {
  const addListener = (id, event, handler) => {
    const element = getElement(id);
    if (element) {
      element.addEventListener(event, handler);
    }
  };

  // Add logo button click handler
  addListener("logoBtn", "click", () => {
    window.location.href = "/";
  });

  const promptInput = getElement("promptInput");
  if (promptInput) {
    promptInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const messageForm = getElement("messageForm");
        if (messageForm) {
          messageForm.dispatchEvent(new Event("submit"));
        }
      }
    });
    promptInput.addEventListener("input", updateSendButtonState); // Update on text input
  }

  addListener("closeSidebarBtn", "click", toggleSidebar);
  addListener("toggleSidebarBtn", "click", toggleSidebar);
  addListener("newChatSidebarBtn", "click", createNewChat);
  addListener("newChatHeaderBtn", "click", createNewChat);

  const settingsBtn = getElement("settingsBtn");
  const settingsDropdown = getElement("settingsDropdown");

  if (settingsBtn && settingsDropdown) {
    settingsBtn.addEventListener("click", () => {
      settingsDropdown.classList.toggle("hidden");
    });

    document.addEventListener("click", (e) => {
      if (
        !settingsBtn.contains(e.target) &&
        !settingsDropdown.contains(e.target)
      ) {
        settingsDropdown.classList.add("hidden");
      }
    });
  }

  addListener("themeToggleBtn", "click", () => {
    const html = document.documentElement;
    const body = document.body;

    // Toggle both html and body classes
    html.classList.toggle("dark");
    body.classList.toggle("dark");

    const isDark = body.classList.contains("dark");
    localStorage.setItem("theme", isDark ? "dark" : "light");

    updateToggleButton();
  });

  addListener("logoutBtn", "click", api.logout);
  addListener("messageForm", "submit", handleSubmit);

  addListener("fileUploadBtn", "click", () => {
    const fileInput = getElement("file-upload");
    if (fileInput) fileInput.click();
  });

  addListener("file-upload", "change", handleFileSelect);

  const welcomeContent = document.querySelector(".welcome-content");
  if (welcomeContent) {
    welcomeContent.addEventListener("click", (e) => {
      const card = e.target.closest(".prompt-card");
      if (card) {
        const text = card.querySelector(".prompt-card-text").textContent;
        const promptInput = getElement("promptInput");
        if (promptInput) {
          promptInput.value = text;
          promptInput.focus();
          updateSendButtonState();
        }
      }
    });
  }

  const chatAreaWrapper = getElement("chatAreaWrapper");
  if (chatAreaWrapper) {
    chatAreaWrapper.addEventListener("dragover", handleDragOver);
    chatAreaWrapper.addEventListener("dragleave", handleDragLeave);
    chatAreaWrapper.addEventListener("drop", handleDrop);
  }

  function openUpgradeModal() {
    const upgradeModal = getElement("upgradeModal");
    if (!upgradeModal) return;

    const planCards = upgradeModal.querySelectorAll(".plan-card");
    planCards.forEach((card) => {
      card.classList.remove("border-2", "border-indigo-500");
    });

    const actionButtons = upgradeModal.querySelectorAll(".plan-button");
    actionButtons.forEach((button) => {
      button.disabled = false;
      if (button.id === "upgradeToProBtn") {
        button.textContent = "Upgrade to Pro";
      }
      button.classList.remove(
        "bg-gray-300",
        "dark:bg-gray-600",
        "text-gray-700",
        "dark:text-gray-300",
        "cursor-default"
      );
      button.classList.add(
        "bg-indigo-600",
        "hover:bg-indigo-700",
        "text-white"
      );
    });

    const defaultCurrentButton = getElement("current-plan-button");
    if (defaultCurrentButton) {
      defaultCurrentButton.disabled = true;
      defaultCurrentButton.textContent = "Your Current Plan";
      defaultCurrentButton.classList.remove("bg-indigo-600", "text-white");
      defaultCurrentButton.classList.add(
        "bg-gray-300",
        "dark:bg-gray-600",
        "text-gray-700",
        "dark:text-gray-300"
      );
    }

    const currentPlanId = `plan-card-${state.user.plan || "free"}`;
    const currentPlanCard = getElement(currentPlanId);
    if (currentPlanCard) {
      currentPlanCard.classList.add("border-2", "border-indigo-500");
      let currentPlanButton;
      if (state.user.plan === "free") {
        currentPlanButton = getElement("current-plan-button");
      } else if (state.user.plan === "pro") {
        currentPlanButton = getElement("upgradeToProBtn");
      }

      if (currentPlanButton) {
        currentPlanButton.disabled = true;
        currentPlanButton.textContent = "Your Current Plan";
        currentPlanButton.classList.remove(
          "bg-indigo-600",
          "hover:bg-indigo-700",
          "text-white"
        );
        currentPlanButton.classList.add(
          "bg-gray-300",
          "dark:bg-gray-600",
          "text-gray-700",
          "dark:text-gray-300",
          "cursor-default"
        );
      }
    }

    upgradeModal.classList.remove("hidden");
  }

  const closeUpgradeModalHandler = () => {
    const upgradeModal = getElement("upgradeModal");
    if (upgradeModal) upgradeModal.classList.add("hidden");
  };

  addListener("upgradeBtn", "click", openUpgradeModal);
  addListener("closeUpgradeModal", "click", closeUpgradeModalHandler);

  const upgradeModal = getElement("upgradeModal");
  if (upgradeModal) {
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !upgradeModal.classList.contains("hidden")) {
        closeUpgradeModalHandler();
      }
    });

    upgradeModal.addEventListener("click", (e) => {
      if (e.target === upgradeModal) {
        closeUpgradeModalHandler();
      }
    });
  }

  addListener("manageSubscriptionBtn", "click", async () => {
    try {
      hideError();
      const response = await api.request("/create-portal-session", {
        method: "POST",
      });
      if (response && response.url) {
        window.location.href = response.url;
      } else {
        throw new Error("Failed to get portal URL.");
      }
    } catch (err) {
      console.error("Error redirecting to portal:", err);
      showError(
        `Error accessing subscription management: ${
          err.message || "Please try again."
        }`
      );
    }
  });

  addListener("addCreditsBtn", "click", () => {
    const addCreditsModal = getElement("addCreditsModal");
    if (addCreditsModal) addCreditsModal.classList.remove("hidden");
  });

  addListener("upgradeToProBtn", "click", () => redirectToCheckout("pro"));

  addListener("buy20CreditsBtn", "click", () =>
    redirectToCheckout("credits_20", true)
  );
  addListener("buy100CreditsBtn", "click", () =>
    redirectToCheckout("credits_100", true)
  );

  addListener("buy500CreditsBtn", "click", () =>
    redirectToCheckout("credits_500", true)
  );

  const chatHistory = getElement("chatHistory");
  if (chatHistory) {
    chatHistory.addEventListener("click", (e) => {
      const deleteButton = e.target.closest("[data-delete-id]");
      if (deleteButton) {
        e.stopPropagation();
        deleteChat(deleteButton.dataset.deleteId, e);
        return;
      }

      const chatButton = e.target.closest("[data-chat-id]");
      if (chatButton) {
        switchChat(chatButton.dataset.chatId);
      }
    });
  }

  window.addEventListener("resize", checkScreenSize);
  window.addEventListener("popstate", handlePopState);

  document.addEventListener("click", (e) => {
    if (state.ui.isSidebarOpen && !state.ui.isMobile) {
      const sidebar = getElement("sidebar");
      const toggleButton = getElement("toggleSidebarBtn");
      const closeButton = getElement("closeSidebarBtn");

      if (
        sidebar &&
        !sidebar.contains(e.target) &&
        toggleButton &&
        !toggleButton.contains(e.target) &&
        (!closeButton || !closeButton.contains(e.target))
      ) {
        toggleSidebar();
      }
    }
  });

  const promptInputForFocus = getElement("promptInput");
  if (promptInputForFocus) {
    promptInputForFocus.addEventListener("focus", () => {
      updateInputPosition();
    });
  }

  // Add Credits Modal handlers
  const closeAddCreditsModalHandler = () => {
    const addCreditsModal = getElement("addCreditsModal");
    if (addCreditsModal) addCreditsModal.classList.add("hidden");
  };

  addListener("closeAddCreditsModal", "click", closeAddCreditsModalHandler);

  const addCreditsModal = getElement("addCreditsModal");
  if (addCreditsModal) {
    addCreditsModal.addEventListener("click", (e) => {
      if (e.target === addCreditsModal) {
        closeAddCreditsModalHandler();
      }
    });
  }

  // Update the existing keydown event listener to handle both modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      const upgradeModal = getElement("upgradeModal");
      const addCreditsModal = getElement("addCreditsModal");

      if (upgradeModal && !upgradeModal.classList.contains("hidden")) {
        closeUpgradeModalHandler();
      }
      if (addCreditsModal && !addCreditsModal.classList.contains("hidden")) {
        closeAddCreditsModalHandler();
      }
    }
  });
}

/**
 * Initialization Functions
 */
async function initializeUI() {
  const setViewportHeight = () => {
    document.documentElement.style.setProperty(
      "--vh",
      `${window.innerHeight * 0.01}px`
    );
  };
  window.addEventListener("resize", setViewportHeight);
  setViewportHeight();

  setupEventListeners();
  checkScreenSize();
  updateSendButtonState(); // Initial call on UI setup

  const modal = createModalElement();
  document.body.appendChild(modal);

  initializeTheme();
}

async function initializeChat() {
  try {
    showElement("loadingIndicator");
    await updateUserCredits();
    await loadAllChats();

    const path = window.location.pathname;
    const parts = path.split("/").filter((p) => p);
    let chatIdFromUrl = null;

    if (path === "/") {
      // Base chat page, chatIdFromUrl remains null.
    } else if (parts.length === 2 && parts[0] === "c") {
      chatIdFromUrl = parts[1];
    }

    if (chatIdFromUrl) {
      await switchChat(chatIdFromUrl);
    } else if (path === "/") {
      const sortedChats = Object.values(state.chat.all).sort(
        (a, b) => getSortTimestamp(b) - getSortTimestamp(a)
      );
      if (sortedChats.length > 0) {
        // Existing user with chats, landing on / without specific chat ID
        state.chat.active = null;
        state.chat.current = null;
        state.chat.messages = [];
        renderMessages();
        updateUrl(null); // This hides the welcome message via its internal call
        showWelcomeMessage(); // Explicitly show welcome message for this case (user has chats but selected none)
      } else {
        // New user (no chats yet) landing on / after signup or directly
        state.chat.active = null;
        state.chat.current = null;
        state.chat.messages = [];
        updateUrl(null); // This ensures the URL is clean and calls hideWelcomeMessage()
        showWelcomeMessage();
        renderMessages(); // This will clear the messages area. Welcome message should be hidden by updateUrl.
      }
    } else {
      showWelcomeMessage(); // Non-chat path, show welcome as a fallback
      updateUrl(null);
    }
    renderChatHistory(); // This might call renderMessages again if logic implies it should focus a chat
  } catch (error) {
    console.error("Error initializing chat:", error);
    showError("Failed to initialize chat. Please try refreshing.");
    updateUrl(null);
    showWelcomeMessage();
  } finally {
    hideElement("loadingIndicator");
    updateSendButtonState();
    focusInput(); // Auto-focus the input when chat initializes
  }
}

async function initialize() {
  try {
    await new Promise((resolve) => {
      const checkElements = () => {
        const requiredIds = [
          "inputArea",
          "toggleSidebarBtn",
          "newChatSidebarBtn",
          "newChatHeaderBtn",
          "messageForm",
          "fileUploadBtn",
          "file-upload",
          "chatAreaWrapper",
          "chatHistory",
          "logoutBtn",
          "closeSidebarBtn",
          "themeToggleBtn",
          "welcomeMessage", // Added welcomeMessage to required IDs
          "messages", // Added messages div, as renderMessages clears it
          "sidebar", // For toggleSidebar
          "mainContent", // For scrollToBottom
        ];

        if (requiredIds.every((id) => getElement(id))) {
          resolve();
        } else {
          setTimeout(checkElements, 100);
        }
      };
      checkElements();
    });

    await initializeUI();
    await initializeChat();
  } catch (err) {
    console.error("Failed to initialize:", err);
    showError("Failed to initialize application");
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initialize);
} else {
  initialize();
}

async function redirectToCheckout(planName, isCreditsPurchase = false) {
  try {
    hideError();

    // Get Paddle configuration from backend
    const paddleConfig = await api.request("/paddle-config");
    if (!paddleConfig) {
      throw new Error("Failed to get Paddle configuration");
    }

    // Get the price ID for the plan
    let priceId;
    if (isCreditsPurchase) {
      priceId = paddleConfig.price_ids[planName];
    } else {
      priceId = paddleConfig.price_ids[planName];
    }

    if (!priceId) {
      throw new Error(
        `Invalid ${isCreditsPurchase ? "credit package" : "plan"}: ${planName}`
      );
    }

    // Initialize Paddle if not already done
    if (typeof Paddle === "undefined") {
      // Load Paddle.js dynamically
      const script = document.createElement("script");
      script.src = "https://cdn.paddle.com/paddle/v2/paddle.js";
      script.onload = () => {
        console.log("Using Paddle Client Token:", paddleConfig.client_token); // Add this log
        Paddle.Environment.set(paddleConfig.environment);
        Paddle.Setup({
          token: paddleConfig.client_token,
          checkout: {
            settings: {
              displayMode: "overlay",
              theme: "light",
              locale: "en",
            },
          },
        });
        openPaddleCheckout(priceId);
      };
      document.head.appendChild(script);
    } else {
      console.log("Using Paddle Client Token:", paddleConfig.client_token); // Add this log
      openPaddleCheckout(priceId);
    }
  } catch (err) {
    console.error("Error setting up checkout:", err);
    showError(
      `Error setting up payment: ${err.message || "Please try again."}`
    );
  }
}

function openPaddleCheckout(priceId) {
  Paddle.Checkout.open({
    items: [{ priceId: priceId, quantity: 1 }],
    customer: {
      email: state.user.email,
    },
    customData: {
      user_id: state.user.user_id,
    },
    settings: {
      displayMode: "overlay",
      theme: "light",
    },
  });
}

async function pollForImageThumbnail(fileId) {
  // Removed originalName as param, fileId should be enough
  let attempts = 0;
  const pollerKey = `imageThumbPoll-${fileId}`;

  // Clear existing poller for this fileId if any
  if (state.activeInputThumbnailPollers[pollerKey]) {
    clearInterval(state.activeInputThumbnailPollers[pollerKey]);
  }

  const intervalId = setInterval(async () => {
    attempts++;
    const currentPreview = state.files.previews.find(
      (p) => p.file_id === fileId // Find by fileId
    );

    if (!currentPreview || attempts > MAX_THUMBNAIL_POLLS) {
      clearInterval(intervalId);
      delete state.activeInputThumbnailPollers[pollerKey];
      if (currentPreview) {
        currentPreview.isLoadingThumbnail = false;
      }
      renderFilePreviews();
      return;
    }

    try {
      const fileInfo = await api.getFileUrl(fileId, true, false);

      if (fileInfo && fileInfo.url) {
        if (fileInfo.url.includes("/thumbnails/")) {
          clearInterval(intervalId);
          delete state.activeInputThumbnailPollers[pollerKey];
          currentPreview.thumbnail = fileInfo.url; // Use fileInfo.url
          currentPreview.isLoadingThumbnail = false;
          renderFilePreviews();
        } else {
          // console.log(`Polled URL for image ${currentPreview.originalName} (ID: ${fileId}) is not a thumbnail URL: ${fileInfo.url}. Retrying...`);
        }
        // Adjusting this else-if condition based on typical structure of getFileUrl response
        // Assuming if it's not a thumbnail URL, it might not have a specific thumbnail_s3_key in this response format,
        // or we simply continue polling if the URL isn't what we expect.
      } else if (
        fileInfo &&
        fileInfo.url &&
        !fileInfo.url.includes("/thumbnails/")
      ) {
        // This case means we got a URL, but it wasn't a thumbnail.
        // console.log(`Still waiting for image thumbnail for ${currentPreview.originalName} (ID: ${fileId}). Received non-thumbnail URL: ${fileInfo.url}. Retrying...`);
      } else if (!fileInfo || !fileInfo.url) {
        // console.log(`No thumbnail URL in getFileUrl response for image ${currentPreview.originalName} (ID: ${fileId}). Retrying... Response:`, fileInfo);
      }
    } catch (error) {
      console.error(
        `Error polling for image thumbnail ${currentPreview?.originalName} (ID: ${fileId}):`,
        error
      );
      if (attempts > MAX_THUMBNAIL_POLLS / 2) {
        clearInterval(intervalId);
        delete state.activeInputThumbnailPollers[pollerKey];
        if (currentPreview) currentPreview.isLoadingThumbnail = false;
        renderFilePreviews();
      }
    }
  }, THUMBNAIL_POLL_INTERVAL);
  state.activeInputThumbnailPollers[pollerKey] = intervalId;
}

async function pollForVideoThumbnail(fileId) {
  let attempts = 0;
  const pollerKey = `videoThumbPoll-${fileId}`;

  if (state.activeInputThumbnailPollers[pollerKey]) {
    clearInterval(state.activeInputThumbnailPollers[pollerKey]);
  }

  const intervalId = setInterval(async () => {
    attempts++;
    const currentPreview = state.files.previews.find(
      (p) => p.file_id === fileId
    );

    if (!currentPreview || attempts > MAX_THUMBNAIL_POLLS) {
      clearInterval(intervalId);
      delete state.activeInputThumbnailPollers[pollerKey];
      if (currentPreview) {
        currentPreview.isLoadingThumbnail = false;
      }
      renderFilePreviews();
      return;
    }

    try {
      const fileInfo = await api.getFileUrl(fileId, true, false);
      if (fileInfo && fileInfo.url) {
        if (fileInfo.url.includes("/thumbnails/")) {
          clearInterval(intervalId);
          delete state.activeInputThumbnailPollers[pollerKey];
          currentPreview.thumbnail = fileInfo.url;
          currentPreview.isLoadingThumbnail = false;
          renderFilePreviews();
        } else {
        }
      } else if (
        fileInfo &&
        fileInfo.url &&
        !fileInfo.url.includes("/thumbnails/")
      ) {
      } else if (!fileInfo || !fileInfo.url) {
      }
    } catch (error) {
      console.error(
        `Error polling for video thumbnail ${currentPreview?.originalName} (ID: ${fileId}):`,
        error
      );
      if (attempts > MAX_THUMBNAIL_POLLS / 2) {
        clearInterval(intervalId);
        delete state.activeInputThumbnailPollers[pollerKey];
        if (currentPreview) currentPreview.isLoadingThumbnail = false;
        renderFilePreviews();
      }
    }
  }, THUMBNAIL_POLL_INTERVAL);
  state.activeInputThumbnailPollers[pollerKey] = intervalId;
}

/**
 * Global Thumbnail Polling for Chat Messages
 */
function isCurrentlyPollingGlobalThumbnail(fileId) {
  return !!state.activeGlobalThumbnailPollers[fileId];
}

async function startGlobalThumbnailPolling(fileId) {
  if (isCurrentlyPollingGlobalThumbnail(fileId)) return;

  let attempts = 0;
  state.activeGlobalThumbnailPollers[fileId] = setInterval(async () => {
    attempts++;
    if (attempts > MAX_GLOBAL_THUMBNAIL_POLLING_ATTEMPTS) {
      clearInterval(state.activeGlobalThumbnailPollers[fileId]);
      delete state.activeGlobalThumbnailPollers[fileId];
      return;
    }

    try {
      const thumbUrlResponse = await api.getFileUrl(fileId, true, false);
      if (
        thumbUrlResponse &&
        thumbUrlResponse.url &&
        !isPlaceholderThumbnail(thumbUrlResponse.url)
      ) {
        const isDedicatedThumbnail =
          thumbUrlResponse.url.includes("/thumbnails/");
        const remainingAttempts =
          MAX_GLOBAL_THUMBNAIL_POLLING_ATTEMPTS - attempts;

        if (
          !isDedicatedThumbnail &&
          remainingAttempts > 0 &&
          attempts <= MAX_GLOBAL_THUMBNAIL_POLLING_ATTEMPTS / 2
        ) {
          return;
        }

        clearInterval(state.activeGlobalThumbnailPollers[fileId]);
        delete state.activeGlobalThumbnailPollers[fileId];

        let updatedChatMessages = false;
        for (const chatIdInState in state.chat.all) {
          if (state.chat.all.hasOwnProperty(chatIdInState)) {
            const chatEntry = state.chat.all[chatIdInState];
            if (
              chatEntry &&
              chatEntry.messages &&
              Array.isArray(chatEntry.messages)
            ) {
              chatEntry.messages.forEach((message) => {
                const filesToUpdate = [];
                if (message.files && Array.isArray(message.files)) {
                  filesToUpdate.push(...message.files);
                }
                if (
                  message.output_files &&
                  Array.isArray(message.output_files)
                ) {
                  filesToUpdate.push(...message.output_files);
                }

                filesToUpdate.forEach((file) => {
                  if (
                    file.file_id === fileId &&
                    file.thumbnail !== thumbUrlResponse.url
                  ) {
                    file.thumbnail = thumbUrlResponse.url;
                    if (chatIdInState === state.chat.active) {
                      updatedChatMessages = true;
                    }
                  }
                });
              });
            }
          }
        }

        if (updatedChatMessages && state.chat.active) {
          state.chat.messages = [...state.chat.all[state.chat.active].messages];
          renderMessages();
        }
      }
    } catch (error) {
      console.error(
        `[startGlobalThumbnailPolling] Error polling for ${fileId}:`,
        error
      );
    }
  }, POLLING_INTERVAL);
}

function getSortTimestamp(chat) {
  // Prioritize updated_at, then created_at, then a very old date if neither exists.
  const dateStr =
    chat.updated_at || chat.created_at || "1970-01-01T00:00:00.000Z";
  return new Date(dateStr).getTime();
}

/**
 * Send Button State Management
 */
function updateSendButtonState() {
  const submitBtn = getElement("submitBtn");
  const promptInput = getElement("promptInput");
  if (!submitBtn || !promptInput) return;

  const promptText = promptInput.value.trim();
  const hasFiles = state.files.previews.length > 0;

  // Check if any files are still being processed or uploaded
  const filesStillProcessing = state.files.previews.some(
    (p) => p.isUploading || p.isLoadingThumbnail || !p.file_id
  );

  // Disable button if:
  // 1. No text and no files, OR
  // 2. Any files are still processing/uploading, OR
  // 3. UI is in loading state
  if (
    (!promptText && !hasFiles) ||
    filesStillProcessing ||
    state.ui.isLoading
  ) {
    submitBtn.disabled = true;
    submitBtn.classList.add("disabled");
    submitBtn.classList.remove("enabled");
  } else {
    submitBtn.disabled = false;
    submitBtn.classList.remove("disabled");
    submitBtn.classList.add("enabled");
  }
}

function focusInput() {
  const promptInput = getElement("promptInput");
  if (promptInput) {
    // Use a small delay to ensure the element is fully rendered and ready
    setTimeout(() => {
      promptInput.focus();
    }, 100);
  }
}
