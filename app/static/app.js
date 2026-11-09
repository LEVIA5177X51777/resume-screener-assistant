// Progressive enhancement for the analysis form and detail page. No framework needed.
(function () {
  "use strict";

  async function readError(res) {
    try {
      const body = await res.json();
      return body.detail ? `${body.error}: ${body.detail}` : body.error || `Request failed (${res.status})`;
    } catch {
      return `Request failed (${res.status})`;
    }
  }

  // ---------- analysis form ----------
  const form = document.getElementById("analyze-form");
  if (form) {
    const maxChars = Number(form.dataset.maxChars);
    const minChars = Number(form.dataset.minChars);
    const errorBox = document.getElementById("form-error");
    const btn = document.getElementById("submit-btn");

    const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };

    const updateCounter = (name) => {
      const el = form.querySelector(`.counter[data-for="${name}"]`);
      const len = form.elements[name].value.trim().length;
      const over = len > maxChars;
      el.textContent = `${len.toLocaleString()} characters` +
        (over ? ` — will be truncated to ${maxChars.toLocaleString()} for analysis` : "");
      el.classList.toggle("over", over);
    };

    ["job_description", "resume"].forEach((name) => {
      form.elements[name].addEventListener("input", () => updateCounter(name));
    });

    form.querySelectorAll('input[type="file"]').forEach((input) => {
      input.addEventListener("change", async () => {
        const file = input.files[0];
        if (!file) return;
        const target = form.elements[input.dataset.target];
        showError("");
        target.value = "Extracting text…";
        const fd = new FormData();
        fd.append("file", file);
        try {
          const res = await fetch("/api/extract-text", { method: "POST", body: fd });
          if (!res.ok) throw new Error(await readError(res));
          target.value = (await res.json()).text;
        } catch (err) {
          target.value = "";
          showError(err.message);
        } finally {
          input.value = "";
          updateCounter(input.dataset.target);
        }
      });
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      showError("");
      const payload = {
        candidate_name: form.elements.candidate_name.value.trim(),
        job_description: form.elements.job_description.value.trim(),
        resume: form.elements.resume.value.trim(),
      };
      // Client-side checks mirror the server's; the server remains the source of truth.
      if (!payload.candidate_name) return showError("Please enter the candidate's name.");
      if (payload.job_description.length < minChars) return showError(`Job description is too short (min ${minChars} characters).`);
      if (payload.resume.length < minChars) return showError(`Resume is too short (min ${minChars} characters).`);

      btn.disabled = true;
      btn.querySelector(".spinner").hidden = false;
      btn.querySelector(".btn-label").textContent = "Analysing… (can take ~20s)";
      try {
        const res = await fetch("/api/analyses", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await readError(res));
        const record = await res.json();
        window.location.href = `/analyses/${record.id}`;
      } catch (err) {
        showError(err.message === "Failed to fetch" ? "Cannot reach the server. Check your connection." : err.message);
        btn.disabled = false;
        btn.querySelector(".spinner").hidden = true;
        btn.querySelector(".btn-label").textContent = "Analyse match";
      }
    });
  }

  // ---------- delete on detail page ----------
  const del = document.getElementById("delete-btn");
  if (del) {
    del.addEventListener("click", async () => {
      if (!confirm("Permanently delete this analysis? This cannot be undone.")) return;
      const res = await fetch(`/api/analyses/${del.dataset.id}`, { method: "DELETE" });
      if (res.ok || res.status === 404) window.location.href = "/history";
      else alert(await readError(res));
    });
  }
})();
