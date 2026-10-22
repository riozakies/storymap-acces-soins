/* Storymap – Atlas sanitaire départemental du Cameroun (MapLibre GL, sans dépendance serveur). */
(function () {
  "use strict";

  // ---------- langue ----------
  let LANG = "fr";
  try {
    const q = new URLSearchParams(location.hash.slice(1)).get("lang");
    LANG = q === "en" || q === "fr" ? q : localStorage.getItem("atlas-lang") || "";
  } catch (e) { LANG = ""; }
  const L = () => window.I18N[LANG || "fr"];
  const t = (k, vars) => String(L()[k] ?? window.I18N.fr[k] ?? k).replace(/\{(\w+)\}/g, (_, v) => (vars && vars[v] !== undefined ? vars[v] : ""));
  const rn = (r) => (L().regions && L().regions[r]) || r;          // nom de région affiché

  // ---------- styles repris du projet QGIS ----------
  const PUBLIC_CAT = [
    ["hg", "Hôpital général (HG)", "#53d9c3"],
    ["hc_chr", "Hôpital central / CHR", "#ef203f"],
    ["hr_hra", "Hôpital régional (HR/HRA)", "#adef28"],
    ["hd", "Hôpital de district (HD)", "#387adc"],
    ["cma", "CMA", "#61dc6c"],
    ["csi", "CSI", "#ecb159"],
    ["infirmerie", "Infirmerie", "#e946d9"],
  ];
  const PUBLIC_OTHER = "#c875c4";
  const PRIVATE = "#8d5a99";
  const ACCESS_CLASSES = [
    [0, 60, "< 60 %", "#b30000"],
    [60, 75, "60 – 75 %", "#e34a33"],
    [75, 90, "75 – 90 %", "#fc8d59"],
    [90, 101, "≥ 90 %", "#fdcc8a"],
  ];
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}";

  const nf = (v) => (v === null || v === undefined || v === "" ? "–" : typeof v === "number" ? v.toLocaleString(L().locale) : v);
  const pct = (v) => (v === null || v === undefined || typeof v !== "number" ? "–" : v.toLocaleString(L().locale, { maximumFractionDigits: 1 }) + " %");
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const accessColor = (v) => {
    if (typeof v !== "number") return "#cccccc";
    for (const [a, b, , c] of ACCESS_CLASSES) if (v >= a && v < b) return c;
    return "#cccccc";
  };
  const IS_PUBLIC = ["in", ["get", "statut_fos"], ["literal", ["public", "parapublic"]]];
  const DATA_V = "4";   // à changer à chaque mise à jour des données (évite les fichiers périmés en cache)
  const isMobile = () => window.matchMedia("(max-width: 860px)").matches;

  // ---------- état ----------
  let ATLAS, DEPTS, BY_SLUG = {}, map, inset, observer, mapReady = false;
  let labelMarkers = [];
  let chapterEls = [], chapterById = {}, scrollLock = 0;

  // chapitre actif = dernier chapitre dont le haut a franchi la ligne de lecture
  function readingLine() {
    const head = $("#masthead").getBoundingClientRect().bottom;
    return head + (window.innerHeight - Math.max(head, 0)) * (isMobile() ? 0.35 : 0.3);
  }
  function onScroll() {
    if (Date.now() < scrollLock || !chapterEls.length) return;
    const y = readingLine();
    let pick = chapterEls[0];
    for (const el of chapterEls) if (el.getBoundingClientRect().top <= y) pick = el;
    activate(chapterById[pick.id]);
  }
  let ticking = false;
  const onScrollRaf = () => { if (!ticking) { ticking = true; requestAnimationFrame(() => { ticking = false; onScroll(); }); } };
  window.addEventListener("scroll", onScrollRaf, { passive: true });
  document.getElementById("panel").addEventListener("scroll", onScrollRaf, { passive: true });
  function updateOffsets() {
    const h = $("#masthead").offsetHeight;
    document.documentElement.style.setProperty("--head-h", h + "px");
  }
  window.addEventListener("resize", updateOffsets);
  const cache = {};
  let current = { region: "", chapter: null };

  const $ = (s) => document.querySelector(s);
  const chaptersEl = $("#chapters");

  // ---------- textes fixes de la page et choix de la langue ----------
  function applyStatic() {
    document.documentElement.lang = LANG || "fr";
    document.title = t("title");
    document.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
    document.querySelectorAll("#lang-switch button").forEach((b) => b.classList.toggle("active", b.dataset.lang === (LANG || "fr")));
    const sel = $("#region-select");
    if (sel) Array.from(sel.options).forEach((o) => (o.textContent = o.value ? rn(o.value) : t("nationalView")));
  }
  function setLang(l) {
    LANG = l;
    try { localStorage.setItem("atlas-lang", l); } catch (e) { /* stockage indisponible */ }
    applyStatic();
    $("#lang-splash").hidden = true;
    if (ATLAS && mapReady) {
      const wasDept = current.chapter && current.chapter.type === "dept" ? current.chapter.dept.slug : null;
      buildLegend(wasDept ? "dept" : "national");
      selectRegion(current.region, wasDept, false);
    }
  }
  document.querySelectorAll("[data-lang]").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
  if (!LANG) $("#lang-splash").hidden = false;
  applyStatic();

  // ---------- démarrage ----------
  Promise.all([
    fetch("data/atlas.json?v=" + DATA_V).then((r) => r.json()),
    fetch("data/departements.json?v=" + DATA_V).then((r) => r.json()),
    fetch("data/cameroun.json?v=" + DATA_V).then((r) => r.json()),
  ]).then(([atlas, depts, pays]) => {
    ATLAS = atlas;
    DEPTS = depts;
    atlas.departements.forEach((d) => (BY_SLUG[d.slug] = d));
    // indicateur d'accessibilité dans la couche nationale (pour la choroplèthe)
    depts.features.forEach((f) => {
      const d = BY_SLUG[f.properties.slug];
      f.properties.pop1h = d && typeof d.pop1h === "number" ? d.pop1h : -1;
      f.properties.color = accessColor(d && d.pop1h);
    });
    const sel = $("#region-select");
    atlas.regions.forEach((r) => {
      const o = document.createElement("option");
      o.value = r;
      o.textContent = rn(r);
      sel.appendChild(o);
    });
    sel.addEventListener("change", () => selectRegion(sel.value, null, true));
    initMap(pays);
  });

  function initMap(pays) {
    map = new maplibregl.Map({
      container: "map",
      style: {
        version: 8,
        sources: {
          esri: { type: "raster", tiles: [ESRI], tileSize: 256, maxzoom: 19, attribution: "Esri, HERE, Garmin, FAO, NOAA, USGS, © OpenStreetMap contributors" },
        },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "#f2efe9" } },
          { id: "esri", type: "raster", source: "esri", paint: { "raster-opacity": 0.85 } },
        ],
      },
      bounds: [8.3, 1.6, 16.3, 13.2],
      fitBoundsOptions: { padding: 30 },
      attributionControl: { compact: true },
      maxBounds: [2, -4, 22, 18],
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-left");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    map.on("load", () => {
      map.addSource("pays", { type: "geojson", data: pays });
      map.addSource("depts", { type: "geojson", data: DEPTS, promoteId: "slug" });
      const empty = { type: "FeatureCollection", features: [] };
      ["communes", "districts", "fosa", "routes", "rivieres"].forEach((s) => map.addSource(s, { type: "geojson", data: empty }));

      // choroplèthe nationale / régionale (% population à 1 h)
      map.addLayer({ id: "dept-choro", type: "fill", source: "depts", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.75 } });
      map.addLayer({ id: "dept-choro-line", type: "line", source: "depts", paint: { "line-color": "#ffffff", "line-width": 0.8 } });
      // masque hors du département (équivalent du polygone inversé de l'atlas)
      map.addLayer({ id: "dept-mask", type: "fill", source: "depts", filter: ["==", ["get", "slug"], ""], paint: { "fill-color": "#ffffff", "fill-opacity": 0.72 } });
      map.addSource("focus-mask", { type: "geojson", data: empty });
      map.addLayer({ id: "focus-mask", type: "fill", source: "focus-mask", paint: { "fill-color": "#ffffff", "fill-opacity": 0.93 } });
      map.addLayer({ id: "rivieres", type: "line", source: "rivieres", paint: { "line-color": "#6498d2", "line-width": ["match", ["get", "waterway"], "river", 1.6, 0.8] } });
      map.addLayer({ id: "routes-sec", type: "line", source: "routes", filter: ["==", ["get", "label_n"], "secondary"], paint: { "line-color": "#fdbf6f", "line-width": 1.6 } });
      map.addLayer({ id: "routes-main", type: "line", source: "routes", filter: ["==", ["get", "label_n"], "main road"], paint: { "line-color": "#000000", "line-width": 2.4 } });
      // zones cliquables (transparentes) et surbrillance de la sélection
      map.addLayer({ id: "communes-fill", type: "fill", source: "communes", paint: { "fill-color": "#000000", "fill-opacity": 0.01 } });
      map.addLayer({ id: "districts-fill", type: "fill", source: "districts", paint: { "fill-color": "#000000", "fill-opacity": 0.01 } });
      map.addLayer({ id: "communes-hl", type: "fill", source: "communes", filter: ["==", ["get", "Nom_commun"], "__aucun__"], paint: { "fill-color": "#28744c", "fill-opacity": 0.16 } });
      map.addLayer({ id: "districts-hl", type: "line", source: "districts", filter: ["==", ["get", "District_S"], "__aucun__"], paint: { "line-color": "#1f78b4", "line-width": 3.5 } });
      map.addLayer({ id: "communes-line", type: "line", source: "communes", paint: { "line-color": "#28744c", "line-width": 1 } });
      map.addLayer({ id: "districts-line", type: "line", source: "districts", paint: { "line-color": "#000000", "line-width": 1.6, "line-dasharray": [1, 1.6] } });
      map.addLayer({ id: "dept-active", type: "line", source: "depts", filter: ["==", ["get", "slug"], ""], paint: { "line-color": "#000000", "line-width": 2.4, "line-dasharray": [4, 2] } });
      map.addLayer({ id: "dept-hover", type: "line", source: "depts", paint: { "line-color": "#1d2a24", "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2.5, 0] } });

      // FOSA privées : cercle creux violet avec point central
      const isPublic = IS_PUBLIC;
      map.addLayer({ id: "fosa-priv", type: "circle", source: "fosa", filter: ["!", isPublic], paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 3, 12, 6], "circle-color": "rgba(0,0,0,0)",
        "circle-stroke-color": PRIVATE, "circle-stroke-width": 1.5 } });
      map.addLayer({ id: "fosa-priv-dot", type: "circle", source: "fosa", filter: ["!", isPublic], paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 1, 12, 2.5], "circle-color": PRIVATE } });
      // FOSA publiques et parapubliques : couleur selon la catégorie
      const catColor = ["match", ["get", "categorie"]];
      PUBLIC_CAT.forEach(([k, , c]) => catColor.push(k, c));
      catColor.push(PUBLIC_OTHER);
      map.addLayer({ id: "fosa-pub", type: "circle", source: "fosa", filter: isPublic, paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 3.5, 12, 7],
        "circle-color": catColor,
        "circle-stroke-color": ["match", ["get", "categorie"], PUBLIC_CAT.map((c) => c[0]), "#33a02c", "#e31a1c"],
        "circle-stroke-width": 1.2 } });

      // zone isolée : voile presque opaque sur tout le reste (limites voisines comprises), puis contour de la zone
      map.moveLayer("focus-mask", "fosa-priv");
      map.addSource("focus-zone", { type: "geojson", data: empty });
      map.addLayer({ id: "focus-zone-line", type: "line", source: "focus-zone",
        paint: { "line-color": "#1f78b4", "line-width": 3 } }, "fosa-priv");

      // interactions
      const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" });
      popup.on("open", () => zonePopup.remove());
      ["fosa-pub", "fosa-priv"].forEach((id) => {
        map.on("click", id, (e) => {
          const p = e.features[0].properties;
          popup.setLngLat(e.lngLat).setHTML(
            `<b>${esc(p.Fosa)}</b><br>${esc(catLabel(p.categorie))} · ${esc(statutLabel(p.statut_fos))}` +
            (p.district ? `<br>${esc(p.district)}` : "") + (p.aire ? `<br>${esc(p.aire)}` : "")
          ).addTo(map);
        });
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
      });
      // clic sur un district de santé / une commune (si aucune FOSA sous le pointeur)
      map.on("click", (e) => {
        if (!current.chapter || current.chapter.type !== "dept") return;
        if (map.queryRenderedFeatures(e.point, { layers: ["fosa-pub", "fosa-priv"] }).length) return;
        const dist = map.queryRenderedFeatures(e.point, { layers: ["districts-fill"] })[0];
        const com = map.queryRenderedFeatures(e.point, { layers: ["communes-fill"] })[0];
        if (!dist && !com) { if (!isolated) clearSelection(); return; }
        showZone(e.lngLat, dist && dist.properties, com && com.properties);
      });
      ["districts-fill", "communes-fill"].forEach((id) => {
        map.on("mousemove", id, () => {
          if (current.chapter && current.chapter.type === "dept") map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
      });
      zonePopup.on("close", () => highlight(null, null));

      let hovered = null;
      map.on("mousemove", "dept-choro", (e) => {
        if (current.chapter && current.chapter.type === "dept") return;
        if (hovered) map.setFeatureState({ source: "depts", id: hovered }, { hover: false });
        hovered = e.features[0].properties.slug;
        map.setFeatureState({ source: "depts", id: hovered }, { hover: true });
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "dept-choro", () => {
        if (hovered) map.setFeatureState({ source: "depts", id: hovered }, { hover: false });
        hovered = null;
        map.getCanvas().style.cursor = "";
      });
      map.on("click", "dept-choro", (e) => {
        if (current.chapter && current.chapter.type === "dept") return;
        const d = BY_SLUG[e.features[0].properties.slug];
        if (d) selectRegion(d.region, d.slug, true);
      });

      mapReady = true;
      const attrib = document.querySelector(".maplibregl-ctrl-attrib");
      if (attrib) attrib.classList.remove("maplibregl-compact-show");
      map.on("moveend", declutter);
      initInset();
      buildLegend("national");
      const st = readHash();
      selectRegion(st.region, st.dept, false);
    });
  }

  function initInset() {
    inset = new maplibregl.Map({
      container: "inset",
      style: {
        version: 8,
        sources: { depts: { type: "geojson", data: DEPTS } },
        layers: [
          { id: "bg", type: "background", paint: { "background-color": "rgba(0,0,0,0)" } },
          { id: "d", type: "fill", source: "depts", paint: { "fill-color": "#e9ece9", "fill-outline-color": "#9aa59f" } },
          { id: "r", type: "fill", source: "depts", filter: ["==", ["get", "region"], ""], paint: { "fill-color": "#b9d6c3" } },
          { id: "a", type: "fill", source: "depts", filter: ["==", ["get", "slug"], ""], paint: { "fill-color": "#1f78b4" } },
        ],
      },
      bounds: [8.4, 1.6, 16.2, 13.1],
      fitBoundsOptions: { padding: 6 },
      interactive: false,
      attributionControl: false,
    });
  }

  // ---------- libellés ----------
  function catLabel(c) { return L().cat[c] || c || "–"; }
  function statutLabel(s) { return L().statut[s] || s || "–"; }
  const classLabel = (c) => (c && L().classif[c]) || c || "–";

  // ---------- légende ----------
  function buildLegend(mode) {
    const b = $("#legend-body");
    if (mode !== "dept") {
      b.innerHTML = `<div class="lg-title">${t("lgAccess")}</div>` +
        ACCESS_CLASSES.map(([, , l, c]) => `<div class="lg-row"><span class="lg-sym"><span class="sq" style="background:${c}"></span></span>${l}</div>`).join("") +
        `<div class="lg-row"><span class="lg-sym"><span class="sq" style="background:#cccccc"></span></span>${t("lgNA")}</div>` +
        `<p class="note" style="margin:6px 0 0">${t("lgClickDept")}</p>`;
      return;
    }
    b.innerHTML =
      `<div class="lg-title">${t("lgPublic")}</div>` +
      PUBLIC_CAT.map(([k, , c]) => `<div class="lg-row"><span class="lg-sym"><span class="dot" style="background:${c};border-color:#33a02c"></span></span>${catLabel(k)}</div>`).join("") +
      `<div class="lg-row"><span class="lg-sym"><span class="dot" style="background:${PUBLIC_OTHER};border-color:#e31a1c"></span></span>${t("lgOtherPublic")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ring"></span></span>${t("lgPrivate")}</div>` +
      `<div class="lg-title">${t("lgLimits")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-top:2px dashed #000"></span></span>${t("department")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-top:2px dotted #000"></span></span>${t("district")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-color:#28744c;border-top-width:1px"></span></span>${t("commune")}</div>` +
      `<div class="lg-title">${t("lgNetwork")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-color:#000;border-top-width:3px"></span></span>${t("mainRoad")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-color:#fdbf6f;border-top-width:2px"></span></span>${t("secRoad")}</div>` +
      `<div class="lg-row"><span class="lg-sym"><span class="ln" style="border-color:#6498d2"></span></span>${t("river")}</div>`;
  }
  $("#legend-toggle").addEventListener("click", () => {
    const l = $("#legend");
    l.classList.toggle("open");
    $("#legend-toggle").setAttribute("aria-expanded", l.classList.contains("open"));
  });
  if (isMobile()) $("#legend").classList.remove("open");

  // ---------- URL ----------
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    const region = ATLAS.regions.includes(p.get("region")) ? p.get("region") : "";
    const dept = BY_SLUG[p.get("dept")] && BY_SLUG[p.get("dept")].region === region ? p.get("dept") : null;
    return { region, dept };
  }
  function writeHash(region, dept) {
    const p = new URLSearchParams();
    if (region) p.set("region", region);
    if (dept) p.set("dept", dept);
    if (LANG && LANG !== "fr") p.set("lang", LANG);
    const h = p.toString();
    history.replaceState(null, "", h ? "#" + h : location.pathname + location.search);
  }
  window.addEventListener("hashchange", () => {
    const st = readHash();
    selectRegion(st.region, st.dept, false);
  });

  // ---------- construction des chapitres ----------
  function selectRegion(region, deptSlug, userAction) {
    current.region = region;
    $("#region-select").value = region;
    const depts = region ? ATLAS.departements.filter((d) => d.region === region) : [];
    const nav = $("#dept-nav");
    current.chapter = null;
    nav.innerHTML = depts.map((d) => `<button data-slug="${d.slug}">${esc(d.nom)}</button>`).join("");
    nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => scrollToChapter("dept-" + b.dataset.slug)));

    const chapters = region ? [regionChapter(region, depts), ...depts.map(deptChapter)] : [nationalChapter()];
    chaptersEl.innerHTML = chapters.map((c) => c.html).join("");
    chaptersEl.querySelectorAll("[data-goto-region]").forEach((el) => el.addEventListener("click", () => selectRegion(el.dataset.gotoRegion, null, true)));
    chaptersEl.querySelectorAll("tr[data-zone]").forEach((el) => el.addEventListener("click", () => {
      const sec = el.closest(".chapter");
      if (current.chapter && current.chapter.id !== sec.id) scrollToChapter(sec.id);
      const go = () => focusZone(el.dataset.zone, el.dataset.name);
      currentData && current.chapter.id === sec.id ? go() : setTimeout(go, 900);
    }));
    chaptersEl.querySelectorAll("[data-goto-dept]").forEach((el) => el.addEventListener("click", () => scrollToChapter("dept-" + el.dataset.gotoDept)));
    const byId = {};
    chapters.forEach((c) => (byId[c.id] = c));

    chapterEls = Array.from(chaptersEl.querySelectorAll(".chapter"));
    chapterById = byId;
    const target = deptSlug ? "dept-" + deptSlug : chapters[0].id;
    activate(byId[target]);
    updateOffsets();
    requestAnimationFrame(() => { if (userAction && !deptSlug) { scrollLock = Date.now() + 150; scrollPanelTop(); } else scrollToChapter(target, true); });
  }

  function scrollPanelTop() {
    if (isMobile()) window.scrollTo({ top: 0, behavior: "instant" });
    else $("#panel").scrollTo({ top: 0, behavior: "instant" });
  }
  function scrollToChapter(id, instant) {
    const el = document.getElementById(id);
    if (!el) return;
    updateOffsets();
    const mobile = isMobile();
    const scroller = mobile ? document.scrollingElement : $("#panel");
    const offset = mobile ? $("#map-wrap").offsetHeight + $("#masthead").offsetHeight + 10 : $("#masthead").offsetHeight + 16;
    const top0 = mobile ? 0 : scroller.getBoundingClientRect().top;
    const target = Math.max(0, scroller.scrollTop + el.getBoundingClientRect().top - top0 - offset);
    activate(chapterFromEl(el));
    const dur = instant ? 0 : 650;
    scrollLock = Date.now() + dur + 120;   // évite d'activer les chapitres traversés
    const start = scroller.scrollTop, t0 = performance.now();
    const step = (t) => {
      const k = dur ? Math.min(1, (t - t0) / dur) : 1;
      scroller.scrollTop = start + (target - start) * (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  let chapterIndex = {};
  function chapterFromEl(el) { return chapterIndex[el.id]; }

  function nationalChapter() {
    const all = ATLAS.departements;
    const sum = (k) => all.reduce((a, d) => a + (d[k] || 0), 0);
    const regions = ATLAS.regions.map((r) => {
      const ds = all.filter((d) => d.region === r);
      return { r, n: ds.length, fosa: ds.reduce((a, d) => a + d.fosa, 0) };
    });
    const c = {
      id: "national", type: "national",
      html: `<section class="chapter" id="national">
        <p class="eyebrow">${t("natEyebrow")}</p>
        <h2>${t("natTitle")}</h2>
        <p>${t("natIntro", { n: all.length })}</p>
        <div class="kpis">
          <div class="kpi"><div class="l">${t("kFosa")}</div><div class="v">${nf(sum("fosa"))}</div></div>
          <div class="kpi"><div class="l">${t("kDistricts")}</div><div class="v">${nf(sum("districts"))}</div></div>
          <div class="kpi"><div class="l">${t("kCommunes")}</div><div class="v">${nf(sum("communes"))}</div></div>
          <div class="kpi"><div class="l">${t("kPop")}</div><div class="v">${nf(sum("population"))}</div></div>
        </div>
        <h3>${t("chooseRegion")}</h3>
        <div class="region-list">${regions.map((x) => `<button data-goto-region="${esc(x.r)}"><b>${esc(rn(x.r))}</b><small>${t("regionItem", { n: x.n, f: nf(x.fosa) })}</small></button>`).join("")}</div>
        <p class="note" style="margin-top:12px">${t("natNote")}</p>
      </section>`,
    };
    chapterIndex = { national: c };
    return c;
  }

  function regionChapter(region, depts) {
    const sum = (k) => depts.reduce((a, d) => a + (d[k] || 0), 0);
    const bbox = depts.reduce((b, d) => [Math.min(b[0], d.bbox[0]), Math.min(b[1], d.bbox[1]), Math.max(b[2], d.bbox[2]), Math.max(b[3], d.bbox[3])], [180, 90, -180, -90]);
    const low = depts.filter((d) => typeof d.pop1h === "number").sort((a, b) => a.pop1h - b.pop1h)[0];
    const rows = depts.map((d) => `<tr class="clickable" data-goto-dept="${d.slug}"><td>${esc(d.nom)}</td><td>${nf(d.fosa)}</td><td>${nf(d.districts)}</td><td>${nf(d.population)}</td><td><span class="pill" style="background:${accessColor(d.pop1h)}33">${pct(d.pop1h)}</span></td></tr>`).join("");
    const c = {
      id: "region", type: "region", region, bbox,
      html: `<section class="chapter" id="region">
        <p class="eyebrow">${t("regEyebrow")}</p>
        <h2>${esc(rn(region))}</h2>
        <p>${t("regIntro", { n: depts.length, d: nf(sum("districts")), f: nf(sum("fosa")), p: nf(sum("population")) })}${low ? t("regLow", { dept: esc(low.nom), v: pct(low.pop1h) }) : ""}</p>
        <h3>${t("departments")}</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>${t("thDept")}</th><th>${t("thFosa")}</th><th>${t("thDistricts")}</th><th>${t("thPop")}</th><th>${t("thPop1h")}</th></tr></thead>
          <tbody>${rows}<tr class="total"><td>${t("total")}</td><td>${nf(sum("fosa"))}</td><td>${nf(sum("districts"))}</td><td>${nf(sum("population"))}</td><td></td></tr></tbody>
        </table></div>
        <p class="note" style="margin-top:10px">${t("regNote")}</p>
      </section>`,
    };
    chapterIndex = { region: c };
    return c;
  }

  function deptChapter(d) {
    const tdist = d.tab_districts;
    const tcom = d.tab_communes;
    const sumCol = (rows, i) => rows.reduce((a, r) => a + (typeof r[i] === "number" ? r[i] : 0), 0);
    const distRows = tdist.map((r) => `<tr class="clickable" data-zone="d" data-name="${esc(r[0])}"><td>${esc(r[0])}</td><td>${nf(r[1])}</td><td>${nf(r[2])}</td><td>${nf(r[3])}</td><td>${nf(r[4])}</td><td>${nf(r[5])}</td><td>${nf(r[6])}</td><td>${esc(classLabel(r[7]))}</td><td>${pct(r[8])}</td><td class="txt">${esc(r[9] || "–")}</td></tr>`).join("");
    const comRows = tcom.map((r) => `<tr class="clickable" data-zone="c" data-name="${esc(r[0])}"><td>${esc(r[0])}</td>${r.slice(1).map((v) => `<td>${nf(v)}</td>`).join("")}</tr>`).join("");
    const comTotal = tcom.length > 1 ? `<tr class="total"><td>${t("total")}</td>${[1, 2, 3, 4, 5, 6].map((i) => `<td>${nf(sumCol(tcom, i))}</td>`).join("")}</tr>` : "";
    const distTotal = tdist.length > 1 ? `<tr class="total"><td>${t("total")}</td><td>${nf(sumCol(tdist, 1))}</td>${[2, 3, 4, 5, 6].map((i) => `<td>${nf(sumCol(tdist, i))}</td>`).join("")}<td></td><td></td><td></td></tr>` : "";
    const p1 = typeof d.pop1h === "number" ? d.pop1h : null;
    const c = {
      id: "dept-" + d.slug, type: "dept", dept: d,
      html: `<section class="chapter" id="dept-${d.slug}">
        <p class="eyebrow">${t("deptEyebrow", { r: esc(rn(d.region)) })}</p>
        <h2>${t("deptTitle", { d: esc(d.nom.toUpperCase()) })}</h2>
        <div class="kpis">
          <div class="kpi"><div class="l">${t("kTotalFosa")}</div><div class="v">${nf(d.fosa)}</div><div class="s">${t("pubPriv", { a: nf(d.fosa_publiques), b: nf(d.fosa_privees) })}</div></div>
          <div class="kpi"><div class="l">${t("kPopEst")}</div><div class="v">${nf(d.population)}</div><div class="s">${t("inhabitants")}</div></div>
          <div class="kpi"><div class="l">${t("kTotalCommunes")}</div><div class="v">${nf(d.communes)}</div></div>
          <div class="kpi"><div class="l">${t("kTotalDistricts")}</div><div class="v">${nf(d.districts)}</div></div>
          <div class="kpi wide"><div class="l">${t("kPop1h")}</div><div class="v">${pct(p1)}</div>
            <div class="bar"><span style="width:${p1 ?? 0}%;background:${accessColor(p1)}"></span></div></div>
        </div>
        <p class="note">${t("clickNote")}</p>
        <h3>${t("tabDistricts")}</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>${t("thDistrict")}</th><th>${t("thPopHab")}</th><th>${t("thPub")}</th><th>HR/HRA</th><th>HD</th><th>CMA</th><th>${t("thCsi")}</th><th>${t("thClass")}</th><th>${t("thPct1h")}</th><th>${t("thComCov")}</th></tr></thead>
          <tbody>${distRows || `<tr><td colspan="10">${t("noData")}</td></tr>`}${distTotal}</tbody>
        </table></div>
        ${tdist.length && sumCol(tdist, 2) !== d.fosa_publiques ? `<p class="note" style="margin-top:6px">${t("distDiff", { a: nf(sumCol(tdist, 2)), b: nf(d.fosa_publiques) })}</p>` : ""}
        <h3>${t("tabCommunes")}</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>${t("thCommune")}</th><th>${t("thPub")}</th><th>${t("thPriv")}</th><th>HR/HRA</th><th>HD</th><th>CMA</th><th>${t("thCsi")}</th></tr></thead>
          <tbody>${comRows || `<tr><td colspan="7">${t("noData")}</td></tr>`}${comTotal}</tbody>
        </table></div>
      </section>`,
    };
    chapterIndex[c.id] = c;
    return c;
  }

  // ---------- activation d'un chapitre : carte ----------
  function activate(ch) {
    if (!ch || (current.chapter && current.chapter.id === ch.id)) return;
    current.chapter = ch;
    chaptersEl.querySelectorAll(".chapter").forEach((el) => el.classList.toggle("active", el.id === ch.id));
    $("#dept-nav").querySelectorAll("button").forEach((b) => b.classList.toggle("active", ch.type === "dept" && b.dataset.slug === ch.dept.slug));
    const navActive = $("#dept-nav button.active");
    if (navActive) navActive.scrollIntoView({ block: "nearest", inline: "nearest" });
    writeHash(current.region, ch.type === "dept" ? ch.dept.slug : null);
    if (!map || !mapReady) return;
    unisolate(false);

    const pad = isMobile() ? 20 : { top: 40, bottom: 40, left: 40, right: 200 };
    if (ch.type === "dept") {
      const d = ch.dept;
      setDeptLayers(true, d);
      map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], { padding: pad, duration: 1400, maxZoom: 12 });
      loadDept(d.slug).then((data) => {
        if (current.chapter !== ch) return;
        currentData = data;
        clearSelection();
        ["communes", "districts", "fosa", "routes", "rivieres"].forEach((k) => map.getSource(k).setData(data[k]));
        setLabels(data.labels.filter((l) => l[1] === "d").concat(data.labels.filter((l) => l[1] === "c")));
      });
      setLabels([]);
      buildLegend("dept");
      setInset(d.region, d.slug);
    } else {
      setDeptLayers(false);
      const empty = { type: "FeatureCollection", features: [] };
      ["communes", "districts", "fosa", "routes", "rivieres"].forEach((k) => map.getSource(k).setData(empty));
      if (ch.type === "region") {
        setLabels(DEPTS.features.filter((f) => f.properties.region === ch.region).map((f) => [f.properties.Nom_Dept, "p", f.properties.lbl[0], f.properties.lbl[1]]));
        map.setFilter("dept-choro", ["==", ["get", "region"], ch.region]);
        map.setFilter("dept-choro-line", ["==", ["get", "region"], ch.region]);
        map.setFilter("dept-mask", ["!=", ["get", "region"], ch.region]);
        map.fitBounds([[ch.bbox[0], ch.bbox[1]], [ch.bbox[2], ch.bbox[3]]], { padding: pad, duration: 1400 });
        setInset(ch.region, "");
      } else {
        ["dept-choro", "dept-choro-line", "dept-mask"].forEach((id) => map.setFilter(id, null));
        map.setFilter("dept-mask", ["==", ["get", "slug"], ""]);
        setLabels([]);
        map.fitBounds([[8.4, 1.6], [16.2, 13.1]], { padding: pad, duration: 1400 });
        setInset("", "");
      }
      buildLegend("national");
    }
  }

  // ---------- sélection d'un district / d'une commune ----------
  const zonePopup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px", className: "zone-popup" });
  let currentData = null;

  function highlight(district, commune) {
    if (!map || !mapReady) return;
    map.setFilter("districts-hl", ["==", ["get", "District_S"], district || "__aucun__"]);
    map.setFilter("communes-hl", ["==", ["get", "Nom_commun"], commune || "__aucun__"]);
  }
  function clearSelection() {
    zonePopup.remove();
    highlight(null, null);
  }
  const row = (l, v) => `<tr><th>${l}</th><td>${v}</td></tr>`;
  function districtHtml(p) {
    return `<div class="zp-kind">${t("district")}</div><div class="zp-name">${esc(p.District_S)}</div><table class="zp">` +
      row(t("zPop"), nf(p.pop) + " " + t("hab")) +
      row(t("zClass"), esc(classLabel(p.classification))) +
      row(t("zPop1h"), pct(p.pop1h)) +
      row(t("zFosa"), `${nf(p.nb_fosa)} <span class="zp-s">(${t("pubPriv", { a: nf(p.nb_pub), b: nf(p.nb_priv) })})</span>`) +
      row(t("zHosp"), `${nf(p.hr_hra)} · ${nf(p.hd)}`) +
      row(t("zCma"), `${nf(p.cma)} · ${nf(p.csi)}`) +
      row(t("zComCov"), esc(p.communes_couvertes || "–")) + "</table>";
  }
  function communeHtml(p) {
    return `<div class="zp-kind c">${t("commune")}</div><div class="zp-name">${esc(p.Nom_commun)}</div><table class="zp">` +
      row(t("zFosa"), nf(p.nb_fosa)) +
      row(t("zPubPriv"), `${nf(p.public)} · ${nf(p.prive)}`) +
      row(t("zHosp"), `${nf(p.hr_hra)} · ${nf(p.hd)}`) +
      row(t("zCma"), `${nf(p.cma)} · ${nf(p.csi)}`) + "</table>";
  }
  function showZone(lngLat, dist, com) {
    highlight(dist && dist.District_S, com && com.Nom_commun);
    const btn = (kind, name, txt) =>
      isolated && isolated.kind === kind && isolated.name === name ? "" :
      `<button class="zp-btn" data-kind="${kind}" data-name="${esc(name)}">${txt}</button>`;
    const parts = [
      dist && districtHtml(dist) + btn("d", dist.District_S, t("onlyDistrict")),
      com && communeHtml(com) + btn("c", com.Nom_commun, t("onlyCommune")),
    ].filter(Boolean);
    zonePopup.setLngLat(lngLat).setHTML(parts.join('<hr class="zp-sep">')).addTo(map);
    zonePopup.getElement().querySelectorAll(".zp-btn").forEach((b) =>
      b.addEventListener("click", () => isolate(b.dataset.kind, b.dataset.name)));
  }

  // ---------- isoler un district ou une commune (comme une région isole ses départements) ----------
  let isolated = null;
  const featureOf = (kind, name) => {
    if (!currentData) return null;
    const src = kind === "d" ? currentData.districts : currentData.communes;
    const key = kind === "d" ? "District_S" : "Nom_commun";
    return src.features.find((x) => x.properties[key] === name) || null;
  };
  function boundsOf(f) {
    const b = new maplibregl.LngLatBounds();
    const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
    walk(f.geometry.coordinates);
    return b;
  }
  function insideFeature(pt, f) {          // le point est-il dans la zone ? (lancer de rayon)
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
    const inRing = (ring) => {
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    };
    return polys.some((pl) => inRing(pl[0]) && !pl.slice(1).some(inRing));
  }
  function invertedMask(f) {
    // grand rectangle percé de la zone : tout ce qui est hors de la zone est voilé de blanc
    const outer = [[0, -10], [30, -10], [30, 20], [0, 20], [0, -10]];
    const polys = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
    return { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [outer, ...polys.map((pl) => pl[0])] } };
  }
  function isolate(kind, name) {
    const f = featureOf(kind, name);
    if (!f) return;
    isolated = { kind, name };
    const pr = f.properties;
    const inList = (pr[kind === "d" ? "communes_in" : "districts_in"] || "").split("|").filter(Boolean);
    const lit = (v) => ["literal", v];
    if (kind === "d") {
      ["districts-line", "districts-fill"].forEach((id) => map.setFilter(id, ["==", ["get", "District_S"], name]));
      ["communes-line", "communes-fill"].forEach((id) => map.setFilter(id, ["in", ["get", "Nom_commun"], lit(inList)]));
    } else {
      ["communes-line", "communes-fill"].forEach((id) => map.setFilter(id, ["==", ["get", "Nom_commun"], name]));
      ["districts-line", "districts-fill"].forEach((id) => map.setFilter(id, ["in", ["get", "District_S"], lit(inList)]));
    }
    const fz = ["==", ["get", kind === "d" ? "ds" : "cm"], name];
    map.setFilter("fosa-pub", ["all", IS_PUBLIC, fz]);
    map.setFilter("fosa-priv", ["all", ["!", IS_PUBLIC], fz]);
    map.setFilter("fosa-priv-dot", ["all", ["!", IS_PUBLIC], fz]);
    map.getSource("focus-mask").setData(invertedMask(f));
    map.getSource("focus-zone").setData(f);
    const keepD = kind === "d" ? [name] : inList, keepC = kind === "c" ? [name] : inList;
    setLabels(currentData.labels.filter((l) => (l[0] === name && l[1] === kind) ||
        (((l[1] === "d" && keepD.includes(l[0])) || (l[1] === "c" && keepC.includes(l[0]))) && insideFeature([l[2], l[3]], f)))
      .sort((a, b) => (a[0] === name ? -1 : b[0] === name ? 1 : 0)));
    map.fitBounds(boundsOf(f), { padding: isMobile() ? 20 : { top: 60, bottom: 40, left: 40, right: 220 }, duration: 900, maxZoom: 13 });
    const lbl = currentData.labels.find((l) => l[1] === kind && l[0] === name);
    showZone({ lng: lbl[2], lat: lbl[3] }, kind === "d" ? pr : null, kind === "c" ? pr : null);
    const bar = $("#focus-bar");
    bar.querySelector("span").textContent = (kind === "d" ? t("focusD") : t("focusC")) + name;
    bar.hidden = false;
  }
  function unisolate(refit = true) {
    if (!isolated) return;
    isolated = null;
    ["districts-line", "districts-fill", "communes-line", "communes-fill"].forEach((id) => map.setFilter(id, null));
    map.setFilter("fosa-pub", IS_PUBLIC);
    map.setFilter("fosa-priv", ["!", IS_PUBLIC]);
    map.setFilter("fosa-priv-dot", ["!", IS_PUBLIC]);
    map.getSource("focus-mask").setData({ type: "FeatureCollection", features: [] });
    map.getSource("focus-zone").setData({ type: "FeatureCollection", features: [] });
    $("#focus-bar").hidden = true;
    clearSelection();
    if (currentData) setLabels(currentData.labels.filter((l) => l[1] === "d").concat(currentData.labels.filter((l) => l[1] === "c")));
    const ch = current.chapter;
    if (refit && ch && ch.type === "dept") {
      const d = ch.dept;
      map.fitBounds([[d.bbox[0], d.bbox[1]], [d.bbox[2], d.bbox[3]]], { padding: isMobile() ? 20 : { top: 40, bottom: 40, left: 40, right: 200 }, duration: 900, maxZoom: 12 });
    }
  }
  $("#focus-back").addEventListener("click", () => unisolate());
  // depuis les tableaux ou les étiquettes : isoler directement la zone
  const focusZone = (kind, name) => isolate(kind, name);

  function setDeptLayers(on, d) {
    const vis = on ? "visible" : "none";
    if (!on) clearSelection();
    ["focus-mask", "focus-zone-line", "rivieres", "routes-sec", "routes-main", "communes-fill", "districts-fill", "communes-hl", "districts-hl", "communes-line", "districts-line", "dept-active", "fosa-priv", "fosa-priv-dot", "fosa-pub"]
      .forEach((id) => map.setLayoutProperty(id, "visibility", vis));
    map.setLayoutProperty("dept-choro", "visibility", on ? "none" : "visible");
    map.setLayoutProperty("dept-choro-line", "visibility", on ? "none" : "visible");
    if (on) {
      map.setFilter("dept-mask", ["!=", ["get", "slug"], d.slug]);
      map.setFilter("dept-active", ["==", ["get", "slug"], d.slug]);
    }
  }

  // étiquettes en HTML, avec suppression des chevauchements (districts prioritaires)
  function setLabels(list) {
    labelMarkers.forEach((m) => m.remove());
    labelMarkers = list.map(([txt, kind, x, y]) => {
      const el = document.createElement("div");
      el.className = "map-label " + kind;
      el.textContent = txt;
      if (kind === "d" || kind === "c") {          // étiquette cliquable : ouvre la fiche
        el.classList.add("clic");
        el.title = kind === "d" ? t("seeDistrict") : t("seeCommune");
        el.addEventListener("click", (ev) => { ev.stopPropagation(); focusZone(kind, txt); });
      }
      return new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([x, y]).addTo(map);
    });
    declutter();
  }
  function declutter() {
    const placed = [];
    labelMarkers.forEach((m) => {
      const el = m.getElement();
      el.classList.remove("hidden");
      const r = el.getBoundingClientRect();
      const hit = placed.some((q) => !(r.right + 4 < q.left || r.left - 4 > q.right || r.bottom + 2 < q.top || r.top - 2 > q.bottom));
      if (hit) el.classList.add("hidden");
      else placed.push(r);
    });
  }

  function setInset(region, slug) {
    if (!inset) return;
    const apply = () => {
      inset.setFilter("r", ["==", ["get", "region"], region]);
      inset.setFilter("a", ["==", ["get", "slug"], slug]);
    };
    inset.isStyleLoaded() ? apply() : inset.once("load", apply);
  }

  function loadDept(slug) {
    if (!cache[slug]) cache[slug] = fetch(`data/dept/${slug}.json?v=${DATA_V}`).then((r) => r.json());
    return cache[slug];
  }
})();
