const stateConfig = {
  idle: {
    label: "Idle",
    robotClass: "robot-button--idle",
  },
  listening: {
    label: "Listening",
    robotClass: "robot-button--listening",
  },
  thinking: {
    label: "Thinking",
    robotClass: "robot-button--thinking",
  },
  responding: {
    label: "Responding",
    robotClass: "robot-button--responding",
  },
  error: {
    label: "Error",
    robotClass: "robot-button--error",
  },
};

const robotButton = document.getElementById("robot-button");
const robotImage = document.getElementById("robot-image");
const statusText = document.getElementById("status-text");
const errorToast = document.getElementById("error-toast");
const memoryToast = document.getElementById("memory-toast");
const secureWarning = document.getElementById("secure-warning");
const uploadButton = document.getElementById("upload-button");
const uploadModal = document.getElementById("upload-modal");
const uploadBackdrop = document.getElementById("upload-backdrop");
const imageInput = document.getElementById("image-input");
const photoPreview = document.getElementById("photo-preview");

let currentState = "idle";
let mediaRecorder = null;
let currentStream = null;
let audioChunks = [];
let conversationHistory = [];
let audioUnlocked = false;
let awaitingManualPlayback = false;
let pendingPlaybackUrl = "";
let audioContext = null;
let currentSourceNode = null;
let pendingImageFile = null;
let pendingImageUrl = "";
const MAX_HISTORY_ITEMS = 4;

function setState(nextState) {
  currentState = nextState;
  const config = stateConfig[nextState];
  robotButton.className = `robot-button ${config.robotClass}`;
  statusText.textContent = config.label;
  updatePhotoPreviewPosition();
}

function showError(message) {
  setState("error");
  errorToast.textContent = message;
  errorToast.classList.remove("hidden");
}

function clearError() {
  errorToast.textContent = "";
  errorToast.classList.add("hidden");
}

function clearAttachedImage() {
  if (pendingImageUrl) {
    URL.revokeObjectURL(pendingImageUrl);
    pendingImageUrl = "";
  }
  pendingImageFile = null;
  imageInput.value = "";
  photoPreview.src = "";
  photoPreview.classList.add("hidden");
  photoPreview.style.top = "";
}

function setUploadModalOpen(isOpen) {
  uploadModal.classList.toggle("hidden", !isOpen);
  uploadModal.setAttribute("aria-hidden", String(!isOpen));
}

function openUploadModal() {
  clearError();
  setUploadModalOpen(true);
}

function updatePhotoPreviewPosition() {
  const rect = robotButton.getBoundingClientRect();
  if (photoPreview.classList.contains("hidden")) {
    return;
  }

  const previewHeight = photoPreview.getBoundingClientRect().height || 76;
  const availableSpace = Math.max(0, window.innerHeight - rect.bottom);
  const top = rect.bottom + (availableSpace / 2) - (previewHeight / 2);
  photoPreview.style.top = `${Math.max(rect.bottom + 12, Math.floor(top))}px`;
}

async function unlockAudioPlayback() {
  if (audioUnlocked) {
    return;
  }

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    throw new Error("This browser does not support Web Audio.");
  }

  audioContext = audioContext || new AudioContextClass();

  try {
    if (audioContext.state !== "running") {
      await audioContext.resume();
    }

    const silentBuffer = audioContext.createBuffer(1, 1, audioContext.sampleRate);
    const source = audioContext.createBufferSource();
    source.buffer = silentBuffer;
    source.connect(audioContext.destination);
    source.start(0);
    source.disconnect();
    audioUnlocked = true;
  } catch (error) {
    throw new Error(`Audio playback is blocked: ${error.message}`);
  }
}

function resetMemoryToast() {
  memoryToast.textContent = "";
  memoryToast.classList.add("hidden");
}

function findSupportedMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];

  if (!window.MediaRecorder) {
    return "";
  }

  for (const candidate of candidates) {
    if (MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return "";
}

function extensionForMimeType(mimeType) {
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("wav")) return "wav";
  return "webm";
}

async function loadStatus() {
  const response = await fetch("/api/status");
  await response.json();
}

async function startRecording() {
  clearError();
  resetMemoryToast();
  await unlockAudioPlayback();

  if (!window.isSecureContext) {
    secureWarning.classList.remove("hidden");
  }

  if (!navigator.mediaDevices?.getUserMedia) {
    showError("This browser cannot access the microphone.");
    return;
  }

  try {
    currentStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    showError(`Microphone access failed: ${error.message}`);
    return;
  }

  audioChunks = [];
  const mimeType = findSupportedMimeType();

  try {
    mediaRecorder = mimeType
      ? new MediaRecorder(currentStream, { mimeType })
      : new MediaRecorder(currentStream);
  } catch (error) {
    stopTracks();
    showError(`Recorder setup failed: ${error.message}`);
    return;
  }

  mediaRecorder.ondataavailable = (event) => {
    if (event.data.size > 0) {
      audioChunks.push(event.data);
    }
  };

  mediaRecorder.onstop = async () => {
    stopTracks();
    const finalMimeType = mediaRecorder.mimeType || mimeType || "audio/webm";
    const blob = new Blob(audioChunks, { type: finalMimeType });
    await sendRecording(blob, finalMimeType);
  };

  mediaRecorder.start();
  setState("listening");
}

function stopTracks() {
  if (currentStream) {
    currentStream.getTracks().forEach((track) => track.stop());
    currentStream = null;
  }
}

function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state !== "recording") {
    return;
  }

  setState("thinking");
  mediaRecorder.stop();
}

async function beginResponding(data) {
  setState("responding");
  pendingPlaybackUrl = `${data.audio_url}?v=${Date.now()}`;

  try {
    awaitingManualPlayback = false;
    await playReplyAudio(pendingPlaybackUrl);
    clearError();
  } catch (error) {
    awaitingManualPlayback = true;
    errorToast.textContent = "Tap the image once to play the reply.";
    errorToast.classList.remove("hidden");
  }
}

async function playReplyAudio(url) {
  if (!audioUnlocked || !audioContext) {
    throw new Error("Audio context is not unlocked.");
  }

  if (audioContext.state !== "running") {
    await audioContext.resume();
  }

  if (currentSourceNode) {
    try {
      currentSourceNode.stop();
    } catch (error) {
      // Ignore stop races on a prior source.
    }
    currentSourceNode.disconnect();
    currentSourceNode = null;
  }

  const response = await fetch(url);
  if (!response.ok) {
    throw new Error("Reply audio could not be downloaded.");
  }

  const audioBuffer = await response.arrayBuffer();
  const decodedBuffer = await audioContext.decodeAudioData(audioBuffer.slice(0));
  const sourceNode = audioContext.createBufferSource();
  sourceNode.buffer = decodedBuffer;
  sourceNode.connect(audioContext.destination);
  currentSourceNode = sourceNode;

  sourceNode.addEventListener("ended", () => {
    if (currentSourceNode === sourceNode) {
      currentSourceNode.disconnect();
      currentSourceNode = null;
      awaitingManualPlayback = false;
      pendingPlaybackUrl = "";
      setState("idle");
    }
  });

  sourceNode.start(0);
}

async function sendRecording(blob, mimeType) {
  const formData = new FormData();
  const extension = extensionForMimeType(mimeType);
  formData.append("audio", blob, `prompt.${extension}`);
  if (pendingImageFile) {
    formData.append("image", pendingImageFile, pendingImageFile.name || "photo.jpg");
  }
  formData.append("history", JSON.stringify(conversationHistory.slice(-MAX_HISTORY_ITEMS)));

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Request failed.");
    }

    conversationHistory.push({ role: "user", content: data.transcript });
    conversationHistory.push({ role: "assistant", content: data.reply_text });

    if (data.memory_saved) {
      memoryToast.textContent = `Saved memory: ${data.memory_saved}`;
      memoryToast.classList.remove("hidden");
    }

    clearAttachedImage();
    await beginResponding(data);
  } catch (error) {
    showError(error.message);
    return;
  }
}

robotButton.addEventListener("click", async () => {
  if (awaitingManualPlayback) {
    try {
      awaitingManualPlayback = false;
      clearError();
      setState("responding");
      await playReplyAudio(pendingPlaybackUrl);
    } catch (error) {
      awaitingManualPlayback = true;
      showError(`Reply audio is still blocked: ${error.message}`);
    }
    return;
  }

  if (currentState === "thinking") {
    return;
  }

  if (currentState === "listening") {
    stopRecording();
    return;
  }

  if (currentSourceNode) {
    try {
      currentSourceNode.stop();
    } catch (error) {
      // Ignore stop races if playback already ended.
    }
  }
  await startRecording();
});

uploadButton.addEventListener("click", openUploadModal);
uploadButton.addEventListener("touchend", (event) => {
  event.preventDefault();
  openUploadModal();
});

uploadBackdrop.addEventListener("click", () => {
  setUploadModalOpen(false);
});

imageInput.addEventListener("change", (event) => {
  const [file] = event.target.files || [];
  setUploadModalOpen(false);
  if (!file) {
    return;
  }
  if (pendingImageUrl) {
    URL.revokeObjectURL(pendingImageUrl);
  }
  pendingImageFile = file;
  pendingImageUrl = URL.createObjectURL(file);
  photoPreview.src = pendingImageUrl;
  photoPreview.classList.remove("hidden");
  updatePhotoPreviewPosition();
  clearError();
  memoryToast.textContent = "Picture attached for the next prompt.";
  memoryToast.classList.remove("hidden");
});

window.addEventListener("load", async () => {
  setState("idle");
  setUploadModalOpen(false);
  if (!window.isSecureContext) {
    secureWarning.classList.remove("hidden");
  }

  try {
    await loadStatus();
  } catch (error) {
    showError(`Status check failed: ${error.message}`);
  }
});

robotImage.addEventListener("load", updatePhotoPreviewPosition);
window.addEventListener("resize", updatePhotoPreviewPosition);
