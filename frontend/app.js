const dropzone = document.getElementById("dropzone");
const dropzoneText = document.getElementById("dropzone-text");
const fileInput = document.getElementById("file-input");
const outputFormat = document.getElementById("output-format");
const form = document.getElementById("upload-form");
const submitBtn = document.getElementById("submit-btn");

const statusSection = document.getElementById("status-section");
const statusText = document.getElementById("status-text");
const progressFill = document.getElementById("progress-fill");
const progressText = document.getElementById("progress-text");
const warningText = document.getElementById("warning-text");
const downloadLink = document.getElementById("download-link");
const errorText = document.getElementById("error-text");

const STATUS_LABELS = {
  pending: "Na fila…",
  translating: "Traduzindo parágrafos…",
  rebuilding: "Remontando o arquivo…",
  converting: "Convertendo formato…",
  done: "Concluído!",
  error: "Erro",
};

let pollTimer = null;

function selectFile(file) {
  if (!file) return;
  const okExt = [".pdf", ".epub", ".txt"].some((ext) =>
    file.name.toLowerCase().endsWith(ext)
  );
  if (!okExt) {
    showError("Formato não suportado. Envie .pdf, .epub ou .txt.");
    return;
  }
  const dt = new DataTransfer();
  dt.items.add(file);
  fileInput.files = dt.files;
  dropzoneText.textContent = file.name;
  dropzone.classList.add("has-file");
  submitBtn.disabled = false;
  hideError();
}

dropzone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  selectFile(file);
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0];
  if (!file) return;

  hideError();
  submitBtn.disabled = true;
  statusSection.hidden = false;
  downloadLink.hidden = true;
  warningText.hidden = true;
  statusText.textContent = "Enviando arquivo…";
  progressFill.style.width = "0%";
  progressText.textContent = "";

  const body = new FormData();
  body.append("file", file);
  if (outputFormat.value) body.append("output_format", outputFormat.value);

  try {
    const res = await fetch("/api/jobs", { method: "POST", body });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Falha ao enviar o arquivo.");
    }
    const job = await res.json();
    pollJob(job.id);
  } catch (err) {
    showError(err.message);
    submitBtn.disabled = false;
  }
});

function pollJob(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) throw new Error("Job não encontrado.");
      const job = await res.json();
      renderJob(job);

      if (job.status === "done" || job.status === "error") {
        clearInterval(pollTimer);
        submitBtn.disabled = false;
        if (job.status === "done") {
          downloadLink.href = `/api/jobs/${jobId}/download`;
          downloadLink.hidden = false;
        }
      }
    } catch (err) {
      clearInterval(pollTimer);
      showError(err.message);
      submitBtn.disabled = false;
    }
  }, 2000);
}

function renderJob(job) {
  statusText.textContent = STATUS_LABELS[job.status] || job.status;

  if (job.progress_total > 0) {
    const pct = Math.round((job.progress_done / job.progress_total) * 100);
    progressFill.style.width = `${pct}%`;
    progressText.textContent = `${job.progress_done} / ${job.progress_total} trechos traduzidos (${pct}%)`;
  } else {
    progressText.textContent = "Analisando arquivo…";
  }

  if (job.warning_message) {
    warningText.textContent = job.warning_message;
    warningText.hidden = false;
  }

  if (job.status === "error") {
    showError(job.error_message || "Ocorreu um erro durante a tradução.");
  }
}

function showError(message) {
  errorText.textContent = message;
  errorText.hidden = false;
}

function hideError() {
  errorText.hidden = true;
}
