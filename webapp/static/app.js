(() => {
  const $ = (s) => document.querySelector(s);
  const form = $("#form"), drop = $("#drop"), fileInput = $("#files"), thumbs = $("#thumbs");
  const MAX = 8;
  let photos = [];           // File objects, in scene order
  let useSample = false;     // bundled sample photos instead of uploads
  let timer = null;

  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderThumbs() {
    thumbs.innerHTML = "";
    if (useSample) {
      $("#sampleRow").hidden = false; $("#useSample").checked = true;
    } else { $("#sampleRow").hidden = true; }
    const items = useSample ? window.__sampleThumbs || [] : photos.map((f) => ({ src: URL.createObjectURL(f), name: f.name }));
    items.forEach((it, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<img alt="${esc(it.name)}" src="${it.src}"><span class="n">${i + 1}</span>` +
        (useSample ? "" : `<button type="button" aria-label="Remove ${esc(it.name)}">&times;</button>`);
      if (!useSample) li.querySelector("button").onclick = () => { photos.splice(i, 1); renderThumbs(); };
      thumbs.appendChild(li);
    });
    $("#photoCount").textContent = `${items.length} of ${MAX}`;
  }

  function addFiles(list) {
    useSample = false;
    for (const f of list) {
      if (!/^image\//.test(f.type) && !/\.(jpe?g|png|webp|bmp)$/i.test(f.name)) continue;
      if (photos.length >= MAX) break;
      photos.push(f);
    }
    renderThumbs();
  }

  drop.onclick = () => fileInput.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } };
  fileInput.onchange = () => { addFiles(fileInput.files); fileInput.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));
  $("#useSample").onchange = (e) => { if (!e.target.checked) { useSample = false; renderThumbs(); } };

  $("#sampleBtn").onclick = async () => {
    const d = await (await fetch("/api/sample")).json();
    for (const [k, v] of Object.entries({ name: d.name, location: d.location, price: d.price, property_type: d.property_type,
      cta_text: d.cta_text, contact: d.contact, brand: d.brand })) form.elements[k].value = v || "";
    form.elements.features.value = d.features.join("\n");
    photos = []; useSample = true;
    window.__sampleThumbs = d.images.map((src) => ({ src, name: src.split("/").pop() }));
    renderThumbs();
  };

  function showErrors(el, problems, head) {
    el.innerHTML = (head ? `<li><strong>${esc(head)}</strong></li>` : "") + (problems || []).map((p) => `<li>${esc(p)}</li>`).join("");
    el.hidden = false;
  }

  function setAspect(aspect) {
    const s = $("#screen");
    s.classList.toggle("vertical", aspect === "9:16");
    s.classList.toggle("square", aspect === "1:1");
  }

  form.onsubmit = async (e) => {
    e.preventDefault();
    $("#formErrors").hidden = true; $("#runErrors").hidden = true; $("#after").hidden = true;
    const fd = new FormData(form);
    ["narration", "music"].forEach((k) => fd.set(k, form.elements[k].checked ? "1" : "0"));
    if (useSample) fd.set("use_sample", "1"); else photos.forEach((f) => fd.append("images", f, f.name));
    setAspect(fd.get("aspect"));
    $("#go").disabled = true; $("#go").textContent = "Creating video...";
    $("#idle").hidden = true; $("#player").hidden = true; $("#busy").hidden = false; $("#barFill").style.width = "2%"; $("#busyMsg").textContent = "Uploading";
    let resp;
    try { resp = await fetch("/api/generate", { method: "POST", body: fd }); }
    catch { return fail(["Could not reach the server."], "Network error"); }
    let data = {}; try { data = await resp.json(); } catch { /* ignore */ }
    if (!resp.ok) return fail(data.problems?.length ? data.problems : [data.error || "Upload failed."], data.error, true);
    poll(data.job_id);
  };

  function fail(problems, head, isForm) {
    clearTimeout(timer);
    $("#busy").hidden = true; $("#idle").hidden = false;
    $("#go").disabled = false; $("#go").textContent = "Create video";
    showErrors(isForm ? $("#formErrors") : $("#runErrors"), problems, head);
  }

  async function poll(id) {
    let j;
    try { j = await (await fetch(`/api/jobs/${id}`)).json(); } catch { return fail(["Lost connection to the server."]); }
    $("#barFill").style.width = Math.max(2, Math.round(j.progress * 100)) + "%";
    $("#busyMsg").textContent = j.message;
    if (j.status === "error") return fail(j.error.problems?.length ? j.error.problems : [], j.error.message);
    if (j.status === "done") return done(j.result);
    timer = setTimeout(() => poll(id), 700);
  }

  function done(r) {
    $("#busy").hidden = true; $("#go").disabled = false; $("#go").textContent = "Create video";
    const v = $("#player"); v.src = r.video + "?t=" + Date.now(); v.hidden = false; v.load();
    $("#dlVideo").href = r.video + "?download=1"; $("#dlSrt").href = r.captions + "?download=1"; $("#dlJson").href = r.script + "?download=1";
    $("#meta").textContent = `${r.duration}s, ${r.size[0]}x${r.size[1]} | script: ${r.writer} | voice-over: ${r.narration || "off"} | music: ${r.music ? "on" : "off"}`;
    $("#warnings").innerHTML = r.warnings.map((w) => `<li>${esc(w)}</li>`).join("");
    $("#scriptTable tbody").innerHTML = r.scenes.map((s, i) =>
      `<tr><td>${i + 1}. ${esc(s.kind)}</td><td>${esc(s.headline)}${s.caption ? "<br><small>" + esc(s.caption) + "</small>" : ""}</td><td>${esc(s.narration) || "-"}</td></tr>`).join("");
    $("#after").hidden = false;
  }
})();
