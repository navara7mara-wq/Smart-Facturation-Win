const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const configPath = process.argv[2] || path.join(root, "visual.config.json");
const outputDir = process.argv[3] || path.join(root, "output", "visual-regression", "current");
const pageId = process.argv[4] || "";
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));

const browserCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

async function applyFixture(page, fixture) {
  if (fixture === "templates") {
    await page.evaluate(() => {
      const layers = document.querySelector(".layers-panel");
      if (layers) {
        const labels = ["Logo Mobilis", "Titre devis", "Logo Entreprise", "Informations site", "Table articles", "Validation Mobilis", "Validation Entreprise"];
        layers.innerHTML = `<h3>Calques</h3>${labels.map((label, index) => `<button type="button" class="${index === 1 ? "active" : ""}"><span>◉</span><span>${label}</span><small>▣</small></button>`).join("")}<footer class="fixture-layer-tools">▱　↑　↓　⌫</footer>`;
      }
      const paper = document.querySelector(".paper-preview");
      if (paper) {
        const byId = (id) => paper.querySelector(`[data-id="${id}"]`);
        const client = byId("client_logo");
        const title = byId("devis_title");
        const company = byId("company_logo");
        const site = byId("site_info");
        const articles = byId("articles_table");
        if (client) {
          client.className = "template-block logo-template-block fixture-template-mobilis";
          client.innerHTML = '<span><small>mobilis</small><strong>mobilis</strong><em>Le futur nous rapproche</em></span>';
          Object.assign(client.style,{left:"41px",top:"31px",width:"162px",height:"64px"});
        }
        if (title) {
          title.classList.add("selected");
          Object.assign(title.style,{left:"225px",top:"23px",width:"366px",height:"86px",fontSize:"16px"});
          const pre = title.querySelector("pre");
          if (pre) pre.textContent = "DEVIS QUANTITATIF CONSTRUCTION\nATTACHEMENT";
        }
        if (company) {
          company.className = "template-block logo-template-block fixture-template-company";
          company.innerHTML = '<span><b>◢</b><strong>SAPTA</strong><small>SOCIÉTÉ D’AMÉNAGEMENT</small></span>';
          Object.assign(company.style,{left:"615px",top:"37px",width:"114px",height:"60px"});
        }
        if (site) {
          site.innerHTML = '<div class="sample-site"><b>Projet</b><b>:</b><span>Extension du réseau FTTH</span><b>Site</b><b>:</b><span>Cité 500 Logements</span><b>Wilaya</b><b>:</b><span>Tizi Ouzou</span><b>Date</b><b>:</b><span>20 mai 2024</span></div>';
          Object.assign(site.style,{left:"41px",top:"125px",width:"720px",height:"136px"});
        }
        if (articles) {
          articles.innerHTML = `<table><thead><tr><th>N°</th><th>Désignation</th><th>Unité</th><th>Quantités</th></tr></thead><tbody>
            <tr class="sample-category"><td colspan="4">FOURNITURES</td></tr>
            <tr><td>1</td><td>Câble à fibre optique 96 FO – Monomode G.652.D</td><td>m</td><td>2 450</td></tr>
            <tr><td>2</td><td>Boîte de jonction optique extérieure 96 FO</td><td>U</td><td>8</td></tr>
            <tr><td>3</td><td>Poteau en béton armé 9/400 – Hauteur 9 m</td><td>U</td><td>32</td></tr>
            <tr><td>4</td><td>Console métallique galva simple</td><td>U</td><td>64</td></tr>
            <tr><td>5</td><td>Fils d’attache et accessoires de fixation</td><td>Ens</td><td>1</td></tr>
            <tr class="sample-category"><td colspan="4">PRESTATION</td></tr>
            <tr><td>6</td><td>Travaux de pose de câble fibre optique<br>aéro-souterrain (fourniture et pose)</td><td>ml</td><td>2 450</td></tr>
            <tr><td>7</td><td>Raccordement et tests de liaison optique</td><td>U</td><td>8</td></tr>
          </tbody></table>`;
          Object.assign(articles.style,{left:"42px",top:"269px",width:"719px",height:"385px"});
        }
        const signatures = document.createElement("div");
        signatures.className = "fixture-template-signatures";
        signatures.innerHTML = '<section><strong>Pour MOBILIS</strong><span>Nom, prénom : ............................</span><span>Fonction : ....................................</span><span>Signature et cachet</span></section><section><strong>Pour SAPTA</strong><span>Nom, prénom : ............................</span><span>Fonction : ....................................</span><span>Signature et cachet</span></section>';
        paper.appendChild(signatures);
      }
      const inspector = document.querySelector(".block-inspector");
      if (inspector) {
        const firstLabel = inspector.querySelector(":scope > label");
        if (firstLabel) firstLabel.classList.add("fixture-hidden-control");
        inspector.querySelectorAll(":scope > .tool-row").forEach((node) => node.classList.add("fixture-hidden-control"));
        const firstGrid = inspector.querySelector(":scope > .grid.mini-grid");
        if (firstGrid) {
          const heading = document.createElement("h4");
          heading.className = "fixture-inspector-heading";
          heading.textContent = "Position";
          firstGrid.before(heading);
        }
      }
    });
    return;
  }
  if (fixture === "entreprise") {
    await page.evaluate(() => {
      ["rgc", "nif", "art", "adresse", "numero_compte"].forEach((name) => {
        const field = document.querySelector(`[name="${name}"]`);
        if (field) field.value = "";
      });
      const preview = document.querySelector(".company-settings .upload-preview");
      if (preview) {
        const img = preview.querySelector("img");
        if (img) img.outerHTML = '<span class="fixture-company-logo"><i>S</i><span><strong>SAPTA</strong><small>Société Algérienne de<br>Production de Tubes en Acier</small></span></span>';
      }
      const audit = document.querySelector(".company-audit");
      if (audit) audit.textContent = "◷  Dernière modification par Administrateur — 31/07/2026 à 10:21";
    });
    return;
  }
  if (fixture === "bpu") {
    await page.evaluate(() => {
      const status = document.querySelector(".bpu-status");
      if (status) {
        const strong = status.querySelector("strong");
        const last = status.querySelector("span:last-child");
        if (strong) strong.textContent = "512 articles";
        if (last) last.textContent = "◷  Dernier import : 31/07/2026 à 09:42 par Administrateur";
      }
      const success = document.querySelector(".import-success");
      if (success) {
        const imported = success.querySelector("b:not(.success-text)");
        const meta = success.querySelector("small:last-child");
        if (imported) imported.textContent = "512 lignes importées";
        if (meta) meta.innerHTML = "31/07/2026 à 09:42<br>par Administrateur";
      }
      const drawerParagraphs = document.querySelectorAll(".bpu-validation > p");
      if (drawerParagraphs[1]) drawerParagraphs[1].textContent = "512 lignes détectées";
      const validation = document.querySelector(".validation-ok span");
      if (validation) validation.innerHTML = "Aucune erreur détectée.<br>Les 512 lignes seront importées.";
      const body = document.querySelector(".bpu-list tbody");
      if (body && body.firstElementChild) {
        const source = body.firstElementChild.cloneNode(true);
        const rows = [
          ["6", "Étude de charge (Note de calcul) certifiée\npar un bureau d'étude agréé", "Forfait", "850 000,00", "Études", "12 BO", "Actif"],
          ["50", "Installation de supports d'antenne", "Unité", "45 000,00", "Installation", "87 BO", "Actif"],
          ["271", "F/P de câble électrique 3G6 mm²", "Mètre", "3 250,00", "Câblage", "34 BO", "Actif"],
          ["470", "Support d'antenne mural galvanisé", "Unité", "18 500,00", "Supports", "56 BO", "Actif"],
          ["471", "Support d'antenne sur mât 1,5 m", "Unité", "27 500,00", "Supports", "43 BO", "Actif"],
          ["672", "Coffret électrique étanche IP65", "Unité", "65 000,00", "Électricité", "29 BO", "Actif"],
          ["815", "Mise à la terre (piquet + câble 16 mm²)", "Unité", "12 750,00", "Électricité", "41 BO", "Actif"],
          ["923", "Peinture anticorrosion des supports", "Forfait", "22 000,00", "Protection", "18 BO", "Actif"],
        ];
        body.innerHTML = "";
        rows.forEach((values) => {
          const row = source.cloneNode(true);
          Array.from(row.cells).forEach((cell, index) => {
            if (index === 3) cell.innerHTML = `<span class="locked-price">▢&nbsp; ${values[index]}</span>`;
            else if (index === 6) cell.innerHTML = `<span class="status-pill">${values[index]}</span>`;
            else cell.textContent = values[index];
          });
          body.appendChild(row);
        });
      }
      const pager = document.querySelector(".bpu-list .pager > span:first-child");
      if (pager) pager.textContent = "1 à 25 sur 512 articles";
    });
    return;
  }
  if (fixture === "mobilis") {
    await page.evaluate(() => {
      const body = document.querySelector(".entity-list tbody");
      if (body && body.firstElementChild) {
        const source = body.firstElementChild.cloneNode(true);
        const rows = [
          ["", "Direction régionale Mobilis Cité Ben Souna", "Algérie Télécom Mobile / Mobilis", "Cité Ben Souna, Lot 01, Bât. 03, Mohammadia, Alger", "16B0991662-00/27", "000716099166279", "24", "57"],
          ["", "Direction régionale Mobilis Alger Centre", "Algérie Télécom Mobile / Mobilis", "32, Rue Didouche Mourad, Alger Centre, Alger", "16B0991662-00/28", "000716099166280", "18", "42"],
          ["", "Direction régionale Mobilis Oran", "Algérie Télécom Mobile / Mobilis", "Hai El Monzah 09, Lotissement N° 123, Bir El Djir, Oran", "16B0991662-00/29", "000716099166281", "16", "36"],
          ["", "Direction régionale Mobilis Chlef", "Algérie Télécom Mobile / Mobilis", "Zone d’Activité, Route Nationale N° 04, Chlef", "16B0991662-00/30", "000716099166282", "15", "31"],
          ["", "Direction régionale Mobilis Sétif", "Algérie Télécom Mobile / Mobilis", "Cité 400 Logements, Bât. Administratif, Sétif", "16B0991662-00/31", "000716099166283", "14", "29"],
        ];
        const logo = '<span class="fixture-mobilis-logo"><small>mobilis</small><strong>mobilis</strong></span>';
        const actions = source.cells[source.cells.length - 1].innerHTML;
        body.innerHTML = "";
        rows.forEach((values, rowIndex) => {
          const row = source.cloneNode(true);
          row.className = rowIndex === 3 ? "selected-row" : "";
          Array.from(row.cells).forEach((cell, cellIndex) => {
            if (cellIndex === 0) cell.innerHTML = logo;
            else if (cellIndex === row.cells.length - 1) cell.innerHTML = actions;
            else cell.textContent = values[cellIndex];
          });
          body.appendChild(row);
        });
      }
      const count = document.querySelector(".page-subtitle span");
      if (count) count.textContent = "8 directions";
      const pager = document.querySelector(".entity-list .pager > span:first-child");
      if (pager) pager.textContent = "Affichage de 1 à 5 sur 8 directions";
      const title = document.querySelector(".entity-drawer .drawer-header h3");
      if (title) title.textContent = "Direction régionale Mobilis Chlef";
      const fields = {
        'input[name="direction_regionale"]': "Direction régionale Mobilis Chlef",
        'textarea[name="adresse"]': "Zone d’Activité, Route Nationale N° 04,\nChlef",
        'input[name="rgc"]': "16B0991662-00/30",
        'input[name="nif"]': "000716099166282",
      };
      Object.entries(fields).forEach(([selector, value]) => { const node = document.querySelector(selector); if (node) node.value = value; });
      const drawerLogo = document.querySelector(".entity-drawer .drawer-upload img");
      if (drawerLogo) {
        drawerLogo.outerHTML = '<span class="fixture-mobilis-drawer-logo"><small>mobilis</small><strong>mobilis</strong></span>';
      }
      const drawerFile = document.querySelector(".entity-drawer .drawer-upload input[type=file]");
      if (drawerFile) {
        const browse = document.createElement("span");
        browse.className = "fixture-mobilis-browse";
        browse.textContent = "Parcourir...";
        drawerFile.replaceWith(browse);
      }
      const upload = document.querySelector(".entity-drawer .drawer-upload");
      if (upload) {
        const note = document.createElement("span");
        note.className = "fixture-mobilis-upload-note";
        note.innerHTML = "JPG, PNG ou GIF<br>Taille max. 2 Mo";
        upload.appendChild(note);
      }
    });
    return;
  }
  if (fixture === "bons-de-commande") {
    await page.evaluate(() => {
      const body = document.querySelector(".po-master-table tbody");
      if (body && body.firstElementChild) {
        const source = body.firstElementChild.cloneNode(true);
        const rows = [
          ["303/2026", "10/02/2026", "régionale Mobilis Cité Ben Souna", "CONSTRUCTION", "2 932 352,78 DZD", "4 sites"],
          ["298/2026", "28/01/2026", "régionale Mobilis Constantine", "RÉNOVATION", "1 487 950,00 DZD", "3 sites"],
          ["287/2026", "15/01/2026", "régionale Mobilis Oran", "CONSTRUCTION", "3 215 600,15 DZD", "5 sites"],
          ["275/2026", "05/01/2026", "régionale Mobilis Alger Centre", "RÉNOVATION", "975 840,00 DZD", "2 sites"],
          ["266/2025", "29/12/2025", "régionale Mobilis Sétif", "CONSTRUCTION", "2 102 450,40 DZD", "4 sites"],
          ["254/2025", "18/12/2025", "régionale Mobilis Blida", "NDC", "1 325 000,00 DZD", "6 sites"],
          ["241/2025", "03/12/2025", "régionale Mobilis Annaba", "RÉNOVATION", "842 120,00 DZD", "2 sites"],
          ["231/2025", "21/11/2025", "régionale Mobilis Tizi Ouzou", "CONSTRUCTION", "2 760 000,00 DZD", "4 sites"],
          ["220/2025", "07/11/2025", "régionale Mobilis Ouargla", "RÉNOVATION", "1 190 560,00 DZD", "3 sites"],
          ["208/2025", "22/10/2025", "régionale Mobilis Bejaia", "CONSTRUCTION", "1 655 780,00 DZD", "4 sites"],
        ];
        body.innerHTML = "";
        rows.forEach((values, index) => {
          const row = source.cloneNode(true);
          row.className = index === 0 ? "selected-row" : "";
          Array.from(row.cells).forEach((cell, cellIndex) => { cell.textContent = values[cellIndex]; });
          body.appendChild(row);
        });
      }
      const pagerText = document.querySelector(".po-master-list .pager > span:first-child");
      if (pagerText) pagerText.textContent = "1 à 10 sur 56";
      const sitesBody = document.querySelector(".po-detail table tbody");
      if (sitesBody && sitesBody.firstElementChild) {
        const source = sitesBody.firstElementChild.cloneNode(true);
        const rows = [
          ["29164", "Cité Ben Souna 4G", "Alger", "Macro", "BET Alpha", "Facturé"],
          ["17451A", "Ben Souna Extension", "Alger", "Micro", "BET Omega", "À facturer"],
          ["29165", "Cité Ben Souna Room", "Alger", "Room", "BET Alpha", "Facturé"],
          ["29166", "Alimentation Énergie", "Alger", "Énergie", "BET Delta", "À facturer"],
        ];
        sitesBody.innerHTML = "";
        rows.forEach((values) => {
          const row = source.cloneNode(true);
          values.forEach((value, cellIndex) => { row.cells[cellIndex].textContent = value; });
          sitesBody.appendChild(row);
        });
      }
      const heading = document.querySelector(".sites-heading h3");
      if (heading) heading.textContent = "Sites du bon de commande (4)";
    });
    return;
  }
  if (fixture === "dashboard") {
    await page.evaluate(() => {
      const values = ["128", "42 850 000,00 DZD", "116", "14"];
      document.querySelectorAll(".dashboard-metrics .metric strong").forEach((node, index) => {
        if (values[index]) node.textContent = values[index];
      });
      const heights = [42, 50, 60, 75, 55, 70, 100, 60, 58, 86, 98, 125];
      const amounts = ["2 950 000", "3 480 000", "4 120 000", "5 260 000", "3 875 000", "4 930 000", "6 750 000", "4 210 000", "3 960 000", "5 980 000", "6 850 000", "8 625 000"];
      document.querySelectorAll(".month-column").forEach((column, index) => {
        const bar = column.querySelector("i");
        const amount = column.querySelector("span");
        if (bar) bar.style.height = `${heights[index]}px`;
        if (amount) amount.textContent = amounts[index];
      });
      const ring = document.querySelector(".donut-ring strong");
      if (ring) ring.textContent = "128";
      const legend = document.querySelector(".donut-legend");
      if (legend) legend.innerHTML = [
        ["#2e9a43", "CONSTRUCTION", "47,1%"],
        ["#337bc1", "ACQUISITION", "24,6%"],
        ["#ef8d19", "CONST/ACQUIS", "17,3%"],
        ["#7b8187", "NDC", "11,0%"],
      ].map(([color, label, qty]) => `<div><i style="background:${color}"></i><span>${label}</span><strong>${qty}</strong></div>`).join("");

      const tableRows = [
        [
          ["FAC/2026/0119", "09/05/2026", "BBA_SIDIKHALED", "1 250 000,00", "18/05/2026"],
          ["FAC/2026/0123", "14/05/2026", "BBA_ELATTAR", "980 000,00", "23/05/2026"],
          ["FAC/2026/0127", "18/05/2026", "BBA_OUEDCHEHAM", "1 560 000,00", "27/05/2026"],
          ["FAC/2026/0131", "21/05/2026", "BBA_MECHERIA", "870 000,00", "30/05/2026"],
          ["FAC/2026/0136", "26/05/2026", "BBA_TENIETELHAAD", "1 120 000,00", "04/06/2026"],
        ],
        [
          ["BBA_ELMAELMA", "BBA_ELMAELMA", "BBA", "10/04/2026"],
          ["BBA_ZOUIA", "BBA_ZOUIA", "BBA", "09/04/2026"],
          ["MCO_TISSEMSILT", "MCO_TISSEMSILT", "MCO", "12/04/2026"],
          ["MCO_KHENCHELA", "MCO_KHENCHELA", "MCO", "08/04/2026"],
          ["BBA_BORDJBOUARRERIDJ", "BBA_BORDJBOUARRERIDJ", "BBA", "07/04/2026"],
        ],
        [
          ["FAC/2026/0142", "28/05/2026", "BBA_BIRMOUHGREIN", "1 450 000,00"],
          ["FAC/2026/0141", "27/05/2026", "MCO_BECHAR", "2 180 000,00"],
          ["FAC/2026/0140", "27/05/2026", "BBA_LAGHOUAT", "1 230 000,00"],
          ["FAC/2026/0139", "26/05/2026", "MCO_OUARGLA", "2 750 000,00"],
          ["FAC/2026/0138", "26/05/2026", "BBA_MASCARA", "980 000,00"],
        ],
      ];
      document.querySelectorAll(".dashboard-list tbody").forEach((body, tableIndex) => {
        body.innerHTML = tableRows[tableIndex].map((row) => `<tr>${row.map((cell, cellIndex) => `<td${(tableIndex === 0 && cellIndex === 4) ? ' class="danger-text"' : ''}>${cell}</td>`).join("")}</tr>`).join("");
      });
      const links = document.querySelectorAll(".dashboard-list > a");
      ["Voir toutes (14)", "Voir toutes (23)", "Voir toutes (20)"].forEach((value, index) => { if (links[index]) links[index].textContent = value; });
    });
    return;
  }
  if (fixture === "table-facturation") {
    await page.evaluate(() => {
      const body = document.querySelector(".tracking-table tbody");
      if (!body || !body.firstElementChild) return;
      const rows = [
        ["SIT-00123", "BETAF", "CONSTRUCTION", "BC Travaux", "BC/25/00123", "F/25/0456", "Alger", "26/05/2025", "2 317 525,00 DZD", "Avancement 4ème situation", "Oui"],
        ["SIT-00124", "INGETEC", "ACQUISITION", "BC Fournitures", "BC/25/00124", "F/25/0457", "Oran", "26/05/2025", "1 845 300,00 DZD", "Livraison partielle équipements", "Oui"],
        ["SIT-00125", "COTECH", "CONST/ACQUIS", "BC Mixte", "BC/25/00125", "F/25/0458", "Constantine", "25/05/2025", "4 560 000,00 DZD", "Matériel livré et pose incluse", "Oui"],
        ["SIT-00126", "BETAF", "CONSTRUCTION", "BC Travaux", "BC/25/00126", "F/25/0459", "Annaba", "25/05/2025", "3 125 450,00 DZD", "Fondations terminées", "Oui"],
        ["SIT-00127", "INGETEC", "ACQUISITION", "BC Fournitures", "BC/25/00127", "F/25/0460", "Blida", "24/05/2025", "875 600,00 DZD", "Lot climatisation", "Non"],
        ["SIT-00128", "COTECH", "CONST/ACQUIS", "BC Mixte", "BC/25/00128", "F/25/0461", "Sétif", "24/05/2025", "2 980 000,00 DZD", "Situation n°2", "Oui"],
        ["SIT-00129", "BETAF", "NDC", "Avenant", "BC/25/00129", "F/25/0462", "Tizi Ouzou", "23/05/2025", "320 000,00 DZD", "Avenant n°1", "Non"],
        ["SIT-00130", "INGETEC", "CONSTRUCTION", "BC Travaux", "BC/25/00130", "F/25/0463", "Ghardaïa", "23/05/2025", "6 750 000,00 DZD", "Gros œuvre terminé", "Oui"],
        ["SIT-00131", "COTECH", "ACQUISITION", "BC Fournitures", "BC/25/00131", "F/25/0464", "Béjaïa", "22/05/2025", "1 235 750,00 DZD", "Fournitures électriques", "Non"],
        ["SIT-00132", "BETAF", "CONST/ACQUIS", "BC Mixte", "BC/25/00132", "F/25/0465", "Mostaganem", "22/05/2025", "3 680 000,00 DZD", "Pose et mise en service", "Oui"],
        ["SIT-00133", "INGETEC", "NDC", "Avenant", "BC/25/00133", "F/25/0466", "Laghouat", "21/05/2025", "415 000,00 DZD", "Avenant prix", "Non"],
        ["SIT-00134", "COTECH", "CONSTRUCTION", "BC Travaux", "BC/25/00134", "F/25/0467", "Alger", "21/05/2025", "5 745 000,00 DZD", "Charpente métallique", "Oui"],
      ];
      const source = body.firstElementChild.cloneNode(true);
      body.innerHTML = "";
      rows.forEach((values, index) => {
        const row = source.cloneNode(true);
        row.className = values[10] === "Oui" ? "depos-ok" : "depos-pending";
        const cells = Array.from(row.cells);
        values.slice(0, 9).forEach((value, cellIndex) => { cells[cellIndex].textContent = value; });
        const remark = cells[9].querySelector("textarea");
        if (remark) remark.value = values[9];
        const status = cells[10].querySelector("select");
        if (status) status.value = values[10] === "Oui" ? "1" : "0";
        body.appendChild(row);
      });
      const summary = document.querySelector(".table-summary strong");
      if (summary) summary.textContent = "128 factures — Total TTC 42 850 000,00 DZD";
      const period = document.querySelector('.tracking-toolbar input[readonly]');
      if (period) period.value = "01/05/2025  →  31/05/2025";
    });
    return;
  }
  if (fixture !== "factures") return;
  await page.evaluate(() => {
    const setValue = (selector, value) => {
      const element = document.querySelector(selector);
      if (element) element.value = value;
    };
    setValue('input[name="invoice_number"]', "137/2026");
    setValue('input[name="invoice_date"]', "2025-05-23");
    setValue('select[name="invoice_type"]', "CONST_ACQUIS");
    const po = document.querySelector('select[name="purchase_order_id"]');
    const poOption = po && Array.from(po.options).find((option) => option.textContent.includes("2259/2025"));
    if (poOption) po.value = poOption.value;
    const site = document.querySelector('select[name="site_id"]');
    const siteOption = site && Array.from(site.options).find((option) => option.textContent.includes("17451A"));
    if (siteOption) site.value = siteOption.value;

    const body = document.querySelector("#invoice-lines-body");
    const addLine = document.querySelector("#add-line");
    const fixtureLines = [
      ["470", "Ciment CPJ CEM II/A-L 42,5N sac 50 kg", "SAC", "880,00", "", "0,00"],
      ["1050", "Sable 0/5 lavé", "M3", "1 650,00", "20,000", "33 000,00"],
      ["2001", "Gravier 15/25 concassé", "M3", "2 200,00", "15,000", "33 000,00"],
      ["", "", "", "0,00", "", "0,00"],
    ];
    if (body && addLine) {
      body.innerHTML = "";
      fixtureLines.forEach((line) => {
        addLine.click();
        const row = body.lastElementChild;
        row.querySelector(".article-input").value = line[0];
        row.querySelector(".designation-cell").textContent = line[1];
        row.querySelector(".unite-cell").textContent = line[2];
        row.querySelector(".pu-cell").textContent = line[3];
        const quantity = row.querySelector(".quantity-input");
        quantity.type = "text";
        quantity.value = line[4];
        row.querySelector(".montant-cell").textContent = line[5];
      });
      body.querySelector(".article-input")?.focus();
    }

    const totals = {
      "#live-total-ht": "66 000,00",
      "#live-rg": "3 300,00",
      "#live-after-rg": "62 700,00",
      "#live-tva": "11 913,00",
      "#live-ttc": "74 613,00",
    };
    Object.entries(totals).forEach(([selector, value]) => {
      const element = document.querySelector(selector);
      if (element) element.textContent = value;
    });

    const savedRows = Array.from(document.querySelectorAll(".invoices-list tbody tr"));
    if (savedRows.length > 2) savedRows[0].remove();
    const visibleRows = Array.from(document.querySelectorAll(".invoices-list tbody tr"));
    const savedFixture = [
      ["136/2026", "CONSTRUCTION", "2259/2025", "17451A - CAC DJELFA", "285 000,00", "339 150,00", null, "K.BENYAHIA", "23/05/2025 10:12"],
      ["135/2026", "CONST/ACQUIS", "2258/2025", "17451A - CAC DJELFA", "182 400,00", "216 056,00", null, "Y.HAMDI", "22/05/2025 16:45"],
    ];
    visibleRows.forEach((row, rowIndex) => {
      const values = savedFixture[rowIndex];
      if (!values) return;
      Array.from(row.cells).forEach((cell, cellIndex) => {
        if (values[cellIndex] !== null && values[cellIndex] !== undefined) {
          cell.textContent = values[cellIndex];
        }
      });
    });
    const pagerText = document.querySelector(".invoices-list .pager > span:first-child");
    if (pagerText) pagerText.textContent = "Affichage 1 à 2 sur 2 entrées";
  });
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const executablePath = browserCandidates.find((candidate) => fs.existsSync(candidate));
  const browser = await chromium.launch({ headless: true, executablePath });

  try {
    for (const item of config.pages.filter((page) => !pageId || page.id === pageId)) {
      const context = await browser.newContext({
        viewport: { width: item.width, height: item.height },
        deviceScaleFactor: 1,
        colorScheme: "light",
        reducedMotion: "reduce",
        locale: "fr-FR",
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });

      await page.goto(new URL(item.route, config.base_url).toString(), {
        waitUntil: "networkidle",
      });
      await page.addStyleTag({
        content: `
          *, *::before, *::after {
            animation-duration: 0s !important;
            animation-delay: 0s !important;
            transition: none !important;
            caret-color: transparent !important;
          }
          html { scroll-behavior: auto !important; }
          ::-webkit-scrollbar { width: 0 !important; height: 0 !important; }
        `,
      });
      await page.evaluate(async () => {
        window.scrollTo(0, 0);
        if (document.fonts && document.fonts.ready) await document.fonts.ready;
        for (const image of Array.from(document.images)) {
          if (!image.complete) {
            await new Promise((resolve) => {
              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            });
          }
        }
      });
      await applyFixture(page, item.fixture);
      await page.screenshot({
        path: path.join(outputDir, `${item.id}.png`),
        fullPage: false,
        animations: "disabled",
      });
      fs.writeFileSync(
        path.join(outputDir, `${item.id}.json`),
        JSON.stringify({ url: page.url(), title: await page.title(), errors }, null, 2),
      );
      console.log(`CAPTURED ${item.id} ${item.width}x${item.height}`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
