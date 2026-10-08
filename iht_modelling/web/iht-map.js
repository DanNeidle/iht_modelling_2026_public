/* Copyright (c) 2026 Tax Policy Associates Ltd
 * Released under the MIT Licence. See LICENCE in the project root. */
/* Constituency IHT exposure map. GeoJSON keys: c code, n name, h household exposure, a resident-adult exposure, m relative wealth, s imputed share. */
(function () {
  "use strict";

  var ROOT = document.getElementById("iht-map");
  if (!ROOT) return;

  var DATA_URL = ROOT.getAttribute("data-geojson");

  // Sequential scale with more detail around the middle of the distribution.
  var BREAKS = [10, 13, 16, 20, 25, 31, 39];
  var COLOURS = [
    "#dcebe8", "#b2d7d3", "#85c0bf", "#56a6ac",
    "#348897", "#1d687b", "#0f4a5d", "#062f3e"
  ];

  function colourFor(value) {
    if (value === undefined || value === null) return "#e4e4e4";
    for (var i = 0; i < BREAKS.length; i++) {
      if (value < BREAKS[i]) return COLOURS[i];
    }
    return COLOURS[COLOURS.length - 1];
  }

  var map, layer, selectedCode = null, featuresByCode = {}, searchIndex = [];
  // Track hover explicitly: Leaflet can miss mouseout after SVG reordering.
  var hoveredCode = null;

  // Headline figures, kept here so the panel and the article agree.
  var NATIONAL = { households: 16.4, bands: null };


  // Freeze hover during animated selection so moving polygons don't replace the readout.
  var ignoreHoverUntil = 0;

  // Frame England and Wales, with southern Scotland for context.
  var GB_BOUNDS = L.latLngBounds([49.92, -5.75], [55.85, 1.78]);

  // rendering

  function style(feature) {
    return {
      fillColor: colourFor(feature.properties.h),
      weight: 0.4,
      color: "#ffffff",
      fillOpacity: 1
    };
  }

  function highlight(target) {
    target.setStyle({ weight: 3, color: "#d0021b", fillOpacity: 1 });
    if (target.bringToFront) target.bringToFront();
  }

  // bringToFront reorders SVG paths and can lose mouseout events. Clear the previous hover on every mouseover.
  function clearHover(except) {
    if (hoveredCode && hoveredCode !== except && featuresByCode[hoveredCode]) {
      resetStyle(featuresByCode[hoveredCode]);
    }
    hoveredCode = null;
  }

  function resetStyle(target) {
    if (target.feature.properties.c === selectedCode) {
      highlight(target);
    } else {
      layer.resetStyle(target);
    }
  }

  // details

  // Bands of bill size, matching BILL_BAND_LABELS in config.py.
  var BAND_LABELS = [
    "No inheritance tax", "Up to \u00a310,000", "\u00a310,000 to \u00a350,000",
    "\u00a350,000 to \u00a3100,000", "\u00a3100,000 to \u00a3200,000",
    "\u00a3200,000 to \u00a3500,000", "\u00a3500,000 or more"
  ];

  function billChart(bands) {
    if (!bands || !bands.length) return "";

    // Use the same 0-100% scale for every bill band.
    var rows = bands.map(function (value, index) {
      var width = value;
      return '<div class="iht-bar-row' + (index === 0 ? " iht-bar-none" : "") + '">' +
        '<span class="iht-bar-label">' + BAND_LABELS[index] + "</span>" +
        '<span class="iht-bar-track"><span class="iht-bar-fill" style="width:' +
        width.toFixed(1) + '%"></span></span>' +
        '<span class="iht-bar-value">' + value.toFixed(1) + "%</span></div>";
    }).join("");

    return '<div class="iht-bars"><h5>Size of the bill, as a share of all ' +
      'over-65 households</h5>' + rows + "</div>";
  }

  function bandsOf(props) {
    var out = [];
    for (var i = 0; i < BAND_LABELS.length; i++) {
      if (props["b" + i] === undefined) return null;
      out.push(props["b" + i]);
    }
    return out;
  }

  function headline(title, share) {
    // The component title already defines the measure.
    return "<h4>" + escapeHtml(title) + "</h4>" +
      '<div class="iht-map-figure">' +
      '<span class="iht-map-big">' + share.toFixed(1) + "%</span>" +
      '<span class="iht-map-define">of pensioner households exposed</span>' +
      "</div>";
  }

  function describe(props) {
    var panel = document.getElementById("iht-map-details");

    if (!props) {
      panel.innerHTML =
        headline("England and Wales", NATIONAL.households) +
        billChart(NATIONAL.bands);
      return;
    }

    if (props.h === undefined) {
      panel.innerHTML =
        "<h4>" + escapeHtml(props.n) + "</h4>" +
        '<p class="iht-map-hint">No estimate for this area.</p>';
      return;
    }

    panel.innerHTML =
      headline(props.n, props.h) +
      billChart(bandsOf(props)) +
      '<dl><dt>Share of all adults here living in such a household</dt><dd>' +
      props.a.toFixed(1) + "%</dd></dl>" +
      suppressionFlag(props.s);
  }

  function suppressionFlag(share) {
    // Flag suppressed counts estimated from regional totals.
    if (share === undefined || share <= 0.1) return "";
    var because = "HMRC withholds a figure where the number of estates is " +
      "small enough to identify the people behind it";
    if (share >= 0.999) {
      return '<p class="iht-map-caveat">Estimated: ' + because +
        ", and it withholds this constituency entirely.</p>";
    }
    if (share > 0.5) {
      return '<p class="iht-map-caveat">Mostly estimated: ' + because +
        ", and most of this constituency is withheld.</p>";
    }
    return '<p class="iht-map-caveat">Part estimated: ' + because +
      ", and part of this constituency is withheld.</p>";
  }

  function onEachFeature(feature, target) {
    featuresByCode[feature.properties.c] = target;
    if (feature.properties.h !== undefined) {
      searchIndex.push({
        name: feature.properties.n,
        lower: fold(feature.properties.n),
        code: feature.properties.c
      });
    }

    target.on({
      mouseover: function (event) {
        if (Date.now() < ignoreHoverUntil) return;
        clearHover(event.target.feature.properties.c);
        hoveredCode = event.target.feature.properties.c;
        highlight(event.target);
        describe(event.target.feature.properties);
      },
      mouseout: function (event) {
        // Clear highlighting even while hover updates are frozen.
        if (hoveredCode === event.target.feature.properties.c) {
          hoveredCode = null;
        }
        resetStyle(event.target);

        if (Date.now() < ignoreHoverUntil) return;
        if (selectedCode) {
          describe(featuresByCode[selectedCode].feature.properties);
        } else {
          describe(null);
        }
      },
      click: function (event) {
        L.DomEvent.stopPropagation(event);
        // Treat shapes without data like the sea: clear selection.
        if (event.target.feature.properties.h === undefined) {
          deselect();
          return;
        }
        // Clicking the selected seat restores the national readout.
        if (event.target.feature.properties.c === selectedCode) {
          deselect();
          // Restore hover while the pointer remains inside the deselected seat.
          highlight(event.target);
          describe(event.target.feature.properties);
          return;
        }
        select(event.target.feature.properties.c, true);
      }
    });
  }

  function deselect() {
    if (selectedCode && featuresByCode[selectedCode]) {
      layer.resetStyle(featuresByCode[selectedCode]);
    }
    clearHover(null);
    selectedCode = null;
    // Clearing selection also ends the hover freeze.
    ignoreHoverUntil = 0;
    describe(null);
  }

  function select(code, zoom) {
    var previous = selectedCode ? featuresByCode[selectedCode] : null;

    clearHover(code);
    selectedCode = code;
    if (previous) layer.resetStyle(previous);

    var target = featuresByCode[code];
    if (!target) return;
    if (zoom) {
      ignoreHoverUntil = Date.now() + 1200;
      map.fitBounds(target.getBounds(), { maxZoom: 10, padding: [20, 20] });
    }
    highlight(target);
    describe(target.feature.properties);
  }

  // search

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function showResults(items) {
    var box = document.getElementById("iht-map-results");
    if (!items.length) {
      box.innerHTML = "";
      box.hidden = true;
      return;
    }
    box.innerHTML = items
      .map(function (item) {
        return '<button type="button" data-code="' +
          escapeHtml(item.code) + '">' + escapeHtml(item.label) + "</button>";
      })
      .join("");
    box.hidden = false;
  }

  // Ignore accents in searches and external constituency names.
  function fold(text) {
    return (text || "").toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  function localMatches(query) {
    var q = fold(query);
    var starts = [], contains = [];
    searchIndex.forEach(function (item) {
      if (item.lower.indexOf(q) === 0) starts.push(item);
      else if (item.lower.indexOf(q) > -1) contains.push(item);
    });
    return starts.concat(contains).slice(0, 8).map(function (item) {
      return { code: item.code, label: item.name };
    });
  }

  var POSTCODE = /^[a-z]{1,2}\d[a-z\d]?\s*\d?[a-z]{0,2}$/i;

  // Ignore stale lookup responses when typing triggers overlapping requests.
  var searchSequence = 0;

  function noCoverage(where) {
    document.getElementById("iht-map-details").innerHTML =
      "<h4>" + escapeHtml(where) + "</h4>" +
      '<p class="iht-map-hint">We have no estimate for this area. HMRC ' +
      "publishes constituency figures for England only, and we have " +
      "estimated Wales from its regional total. Scotland and Northern " +
      "Ireland are not covered.</p>";
  }

  function remoteSearch(query) {
    // postcodes.io returns a constituency for postcodes; reverse-lookup coordinates for places.
    var clean = query.replace(/\s+/g, "");
    var url = POSTCODE.test(clean)
      ? "https://api.postcodes.io/postcodes/" + encodeURIComponent(clean)
      : "https://api.postcodes.io/places?q=" + encodeURIComponent(query) + "&limit=5";

    searchSequence += 1;
    var sequence = searchSequence;

    fetch(url)
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        if (sequence !== searchSequence) return;   // a newer search has started
        var result = payload.result;
        if (!result) return;

        if (!Array.isArray(result)) {
          var name = result.parliamentary_constituency_2024 ||
                     result.parliamentary_constituency;
          var folded = fold(name);
          var match = searchIndex.filter(function (item) {
            return item.lower === folded;
          })[0];
          if (!match && name) {
            // A name we cannot match is a data problem, not a coverage gap.
            if (window.console) console.warn("Unmatched constituency:", name);
          }
          showResults([]);
          document.getElementById("iht-map-search").value = result.postcode;
          if (match) {
            select(match.code, true);
          } else {
            noCoverage(name || result.postcode);
          }
          return;
        }

        // Places give coordinates, so find whichever seat contains the point.
        var options = [];
        result.forEach(function (place) {
          var code = codeAt(place.longitude, place.latitude);
          if (code) {
            options.push({
              code: code,
              label: place.name_1 + " (" + featuresByCode[code].feature.properties.n + ")"
            });
          }
        });
        if (options.length) {
          showResults(options);
        } else if (result.length) {
          showResults([]);
          noCoverage(result[0].name_1);
        }
      })
      .catch(function () { /* offline or rate limited: local search still works */ });
  }

  function codeAt(longitude, latitude) {
    var point = L.latLng(latitude, longitude);
    var found = null;
    layer.eachLayer(function (target) {
      if (found) return;
      if (target.getBounds().contains(point) &&
          pointInFeature(longitude, latitude, target.feature)) {
        found = target.feature.properties.c;
      }
    });
    return found;
  }

  function pointInFeature(x, y, feature) {
    var polygons = feature.geometry.type === "Polygon"
      ? [feature.geometry.coordinates]
      : feature.geometry.coordinates;
    for (var p = 0; p < polygons.length; p++) {
      if (pointInRings(x, y, polygons[p])) return true;
    }
    return false;
  }

  function pointInRings(x, y, rings) {
    if (!ringContains(x, y, rings[0])) return false;
    for (var h = 1; h < rings.length; h++) {
      if (ringContains(x, y, rings[h])) return false;   // inside a hole
    }
    return true;
  }

  function ringContains(x, y, ring) {
    var inside = false;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      var xi = ring[i][0], yi = ring[i][1];
      var xj = ring[j][0], yj = ring[j][1];
      if (((yi > y) !== (yj > y)) &&
          (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
        inside = !inside;
      }
    }
    return inside;
  }

  // init

  function buildLegend() {
    var legend = L.control({ position: "bottomright" });
    legend.onAdd = function () {
      var div = L.DomUtil.create("div", "iht-map-legend");
      var html = "<strong>% of pensioner<br>households</strong>";
      var lower = 0;
      for (var i = 0; i < COLOURS.length; i++) {
        var upper = BREAKS[i];
        html += '<span><i style="background:' + COLOURS[i] + '"></i>' +
          (upper === undefined ? lower + "%+" : lower + " to " + upper + "%") +
          "</span>";
        lower = upper;
      }
      div.innerHTML = html;
      return div;
    };
    legend.addTo(map);
  }

  function init(geojson) {
    map = L.map("iht-map-canvas", {
      attributionControl: false,
      // Keep whole-level Leaflet zooms: fractional steps lose wheel movement during animations and blur raster tiles.
      minZoom: 5,
      maxZoom: 12,
      // Keep panning within bounds to avoid snap-back colliding with zoom.
      maxBounds: GB_BOUNDS.pad(0.3),
      maxBoundsViscosity: 1.0
    });

    // Esri's light grey basemap requires no API key.
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
      "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 12,
        // If Esri ever stops serving these, the shading still has to read.
        errorTileUrl:
          "data:image/gif;base64,R0lGODlhAQABAIAAAPLy8gAAACH5BAAAAAAALAAAAAABAAEAAAICRAEAOw=="
      }
    ).addTo(map);

    // Read national figures from the same file as the map.
    if (geojson.national) {
      NATIONAL.households = geojson.national.h;
      NATIONAL.bands = geojson.national.bands;
    }

    layer = L.geoJSON(geojson, { style: style, onEachFeature: onEachFeature })
      .addTo(map);

    // Frame the area with estimates.
    map.fitBounds(GB_BOUNDS, { padding: [6, 6] });
    buildLegend();
    describe(null);

    // Expose the map for browser-console checks.
    window.ihtMap = map;

    // Clicking outside a seat restores national figures.
    map.on("click", function () {
      if (selectedCode) deselect();
    });

    // Container mouseleave clears hover when exiting the map, without firing between polygons.
    map.getContainer().addEventListener("mouseleave", function () {
      clearHover(null);
      if (Date.now() < ignoreHoverUntil) return;
      describe(selectedCode ? featuresByCode[selectedCode].feature.properties : null);
    });

    var input = document.getElementById("iht-map-search");
    var timer;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var query = input.value.trim();
      if (query.length < 2) { showResults([]); return; }
      showResults(localMatches(query));
      timer = setTimeout(function () {
        if (localMatches(query).length === 0) remoteSearch(query);
      }, 350);
    });

    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      event.preventDefault();
      var first = document.querySelector("#iht-map-results button");
      if (first) first.click();
      else remoteSearch(input.value.trim());
    });

    document.getElementById("iht-map-results")
      .addEventListener("click", function (event) {
        var button = event.target.closest("button");
        if (!button) return;
        input.value = button.textContent;
        showResults([]);
        select(button.getAttribute("data-code"), true);
      });

    document.getElementById("iht-map-reset")
      .addEventListener("click", function () {
        input.value = "";
        showResults([]);
        deselect();
        map.fitBounds(GB_BOUNDS, { padding: [6, 6] });
      });
  }

  fetch(DATA_URL)
    .then(function (response) { return response.json(); })
    .then(init)
    .catch(function () {
      ROOT.innerHTML =
        '<p class="iht-map-hint">The map could not be loaded. Every ' +
        'constituency figure is in the <a href="https://taxpolicy.org.uk/' +
        'wp-content/assets/iht/inheritance-tax-by-constituency.csv">' +
        "downloadable table</a>.</p>";
    });
})();
