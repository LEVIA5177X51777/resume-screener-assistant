(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const ICON_CHECK = '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
  const ICON_ALERT = '<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>';

  function toast(message, type = "success") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = type === "error" ? ICON_ALERT : ICON_CHECK;
    const text = document.createElement("span");
    text.textContent = message;
    el.appendChild(text);
    $("#toasts").appendChild(el);
    setTimeout(() => {
      el.classList.add("leaving");
      el.addEventListener("animationend", () => el.remove(), { once: true });
    }, type === "error" ? 5000 : 2600);
  }

  async function readError(res) {
    try {
      const body = await res.json();
      return body.detail ? `${body.error}: ${body.detail}` : body.error || `Request failed (${res.status})`;
    } catch {
      return `Request failed (${res.status})`;
    }
  }

  async function copy(text, label = "Copied to clipboard") {
    try {
      await navigator.clipboard.writeText(text);
      toast(label);
    } catch {
      toast("Clipboard is not available in this browser", "error");
    }
  }

  const storage = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch {} },
  };

  function initShell() {
    const root = document.documentElement;
    $("#theme-toggle")?.addEventListener("click", () => {
      const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      storage.set("theme", next);
    });

    const sidebar = $("#sidebar");
    const scrim = $("#scrim");
    const setMenu = (open) => { sidebar.classList.toggle("open", open); scrim.classList.toggle("open", open); };
    $("#menu-btn")?.addEventListener("click", () => setMenu(true));
    scrim?.addEventListener("click", () => setMenu(false));

    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || e.metaKey || e.ctrlKey || e.altKey;
      if (e.key === "Escape") setMenu(false);
      if (typing) return;
      if (e.key === "n") location.href = "/";
      else if (e.key === "h") location.href = "/history";
      else if (e.key === "t") $("#theme-toggle")?.click();
      else if (e.key === "/" && $(".search input")) { e.preventDefault(); $(".search input").focus(); }
    });

    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    const units = [["year", 31536000], ["month", 2592000], ["week", 604800], ["day", 86400], ["hour", 3600], ["minute", 60]];
    $$("time[data-ts]").forEach((el) => {
      const date = new Date(el.dataset.ts);
      if (Number.isNaN(date.getTime())) return;
      el.title = date.toLocaleString();
      const diff = (date.getTime() - Date.now()) / 1000;
      const [unit, secs] = units.find(([, s]) => Math.abs(diff) >= s) || ["second", 1];
      el.textContent = Math.abs(diff) < 45 ? "just now" : rtf.format(Math.round(diff / secs), unit);
    });

    $$("[data-count]").forEach((el) => {
      const target = Number(el.dataset.count);
      if (!Number.isFinite(target) || target === 0 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      const start = performance.now();
      const duration = 1100;
      const tick = (now) => {
        const p = Math.min((now - start) / duration, 1);
        el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(tick);
      };
      el.textContent = "0";
      requestAnimationFrame(tick);
    });

    const params = new URLSearchParams(location.search);
    if (params.get("deleted")) {
      toast("Analysis deleted");
      params.delete("deleted");
      history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : ""));
    }
  }

  function initAnalyzeForm() {
    const form = $("#analyze-form");
    if (!form) return;
    const maxChars = Number(form.dataset.maxChars);
    const minChars = Number(form.dataset.minChars);
    const errorBox = $("#form-error");
    const submitBtn = $("#submit-btn");
    const overlay = $("#progress");
    const fmt = new Intl.NumberFormat();

    const showError = (msg) => {
      $(".msg", errorBox).textContent = msg || "";
      errorBox.hidden = !msg;
      if (msg) errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
    };

    const updateMeter = (name) => {
      const meter = $(`.meter[data-for="${name}"]`);
      const len = form.elements[name].value.trim().length;
      const over = len > maxChars;
      $(".meter-bar span", meter).style.width = `${Math.min(len / maxChars, 1) * 100}%`;
      $(".meter-text", meter).textContent = over
        ? `${fmt.format(len)} chars · will be truncated`
        : `${fmt.format(len)} / ${fmt.format(maxChars)}`;
      meter.classList.toggle("over", over);
    };

    const setText = (name, text) => {
      form.elements[name].value = text;
      updateMeter(name);
    };

    async function uploadFile(name, file) {
      const zone = $(`.dropzone[data-target="${name}"]`);
      showError("");
      zone.classList.add("loading");
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await fetch("/api/extract-text", { method: "POST", body: fd });
        if (!res.ok) throw new Error(await readError(res));
        setText(name, (await res.json()).text);
        toast(`Extracted text from ${file.name}`);
      } catch (err) {
        showError(err.message);
      } finally {
        zone.classList.remove("loading");
      }
    }

    ["job_description", "resume"].forEach((name) => {
      form.elements[name].addEventListener("input", () => updateMeter(name));
      updateMeter(name);
    });

    $$('input[type="file"]', form).forEach((input) => {
      input.addEventListener("change", () => {
        if (input.files[0]) uploadFile(input.dataset.target, input.files[0]);
        input.value = "";
      });
    });

    $$(".dropzone", form).forEach((zone) => {
      let depth = 0;
      zone.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; zone.classList.add("dragging"); });
      zone.addEventListener("dragover", (e) => e.preventDefault());
      zone.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; zone.classList.remove("dragging"); } });
      zone.addEventListener("drop", (e) => {
        e.preventDefault();
        depth = 0;
        zone.classList.remove("dragging");
        const file = e.dataTransfer.files[0];
        if (file) uploadFile(zone.dataset.target, file);
      });
    });

    $$("[data-sample]", form).forEach((btn) => {
      btn.addEventListener("click", async () => {
        try {
          const res = await fetch(`/api/samples/${btn.dataset.sample}`);
          if (!res.ok) throw new Error(await readError(res));
          const data = await res.json();
          setText(btn.dataset.target, data.text);
          if (data.candidate_name) form.elements.candidate_name.value = data.candidate_name;
          toast(`Loaded sample: ${btn.textContent.trim()}`);
        } catch (err) {
          showError(err.message);
        }
      });
    });

    $$("[data-clear]", form).forEach((btn) => {
      btn.addEventListener("click", () => { setText(btn.dataset.clear, ""); form.elements[btn.dataset.clear].focus(); });
    });

    form.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); }
    });

    let stepTimer = null;
    const startProgress = () => {
      const steps = $$(".progress-steps li", overlay);
      steps.forEach((s) => s.classList.remove("active", "done"));
      overlay.hidden = false;
      let i = 0;
      steps[0].classList.add("active");
      stepTimer = setInterval(() => {
        if (i >= steps.length - 2) return;
        steps[i].classList.replace("active", "done");
        steps[++i].classList.add("active");
      }, 900);
    };
    const finishProgress = async (ok) => {
      clearInterval(stepTimer);
      if (ok) {
        const steps = $$(".progress-steps li", overlay);
        steps.forEach((s) => { s.classList.remove("active"); s.classList.add("done"); });
        await new Promise((r) => setTimeout(r, 450));
      } else {
        overlay.hidden = true;
      }
    };

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      showError("");
      const payload = {
        candidate_name: form.elements.candidate_name.value.trim(),
        job_description: form.elements.job_description.value.trim(),
        resume: form.elements.resume.value.trim(),
      };
      if (!payload.candidate_name) { form.elements.candidate_name.focus(); return showError("Please enter the candidate's name."); }
      if (payload.job_description.length < minChars) return showError(`The job description is too short (minimum ${minChars} characters).`);
      if (payload.resume.length < minChars) return showError(`The resume is too short (minimum ${minChars} characters).`);

      submitBtn.disabled = true;
      startProgress();
      try {
        const res = await fetch("/api/analyses", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) throw new Error(await readError(res));
        const record = await res.json();
        await finishProgress(true);
        location.href = `/analyses/${record.id}`;
      } catch (err) {
        await finishProgress(false);
        showError(err.message === "Failed to fetch" ? "Cannot reach the server. Check your connection and try again." : err.message);
        submitBtn.disabled = false;
      }
    });
  }

  function initReport() {
    const dataEl = $("#report-data");
    if (!dataEl) return;
    const record = JSON.parse(dataEl.textContent);
    const r = record.report;

    $("[data-copy-report]")?.addEventListener("click", () => {
      const lines = [
        `${record.candidate_name} — match score ${r.score}/100`,
        "",
        r.summary,
        `Why: ${r.score_rationale}`,
        "",
        "Top matching skills:",
        ...r.matching_skills.map((s, i) => `  ${i + 1}. ${s.skill} — ${s.evidence}`),
        "",
        "Top gaps:",
        ...r.gaps.map((g, i) => `  ${i + 1}. ${g}`),
        "",
        "Interview questions:",
        ...r.interview_questions.map((q, i) => `  ${i + 1}. ${q}`),
      ];
      copy(lines.join("\n"), "Report copied to clipboard");
    });

    $("[data-copy-questions]")?.addEventListener("click", () =>
      copy(r.interview_questions.map((q, i) => `${i + 1}. ${q}`).join("\n"), "Questions copied"));

    $$("[data-copy-text]").forEach((btn) => btn.addEventListener("click", () => copy(btn.dataset.copyText, "Question copied")));

    const dialog = $("#delete-dialog");
    $("[data-delete]")?.addEventListener("click", (e) => {
      $("#delete-name").textContent = e.currentTarget.dataset.name;
      dialog.showModal();
    });
    dialog?.addEventListener("close", async () => {
      if (dialog.returnValue !== "confirm") return;
      const res = await fetch(`/api/analyses/${record.id}`, { method: "DELETE" });
      if (res.ok || res.status === 404) location.href = "/history?deleted=1";
      else toast(await readError(res), "error");
    });
  }

  function initHistory() {
    $$("tr[data-href]").forEach((row) => {
      row.addEventListener("click", (e) => { if (!e.target.closest("a")) location.href = row.dataset.href; });
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") location.href = row.dataset.href; });
    });
    $('#filter-form select[name="sort"]')?.addEventListener("change", (e) => e.target.form.submit());
  }

  initShell();
  initAnalyzeForm();
  initReport();
  initHistory();
})();
