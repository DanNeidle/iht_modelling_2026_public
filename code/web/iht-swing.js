/* Copyright (c) 2026 Tax Policy Associates Ltd
 * Released under the MIT Licence. See LICENCE in the project root. */
/*
 * What happens to the Commons if pensioners facing an inheritance tax bill
 * switch to the Conservatives.
 *
 * Three controls. How many exposed pensioners switch their vote, how many of
 * those who did not vote in 2024 turn out this time, and how big a bill has to
 * be before either of those applies.
 *
 * The arithmetic runs here rather than on a server, because it is 575 seats
 * times seven parties and the slider has to keep up with a thumb.
 *
 * Nothing about the result is written into this file. The line under the
 * chamber is built from whatever the model returns, so if the answer ever
 * changes the page says something different without anyone editing it.
 */
(function () {
  "use strict";

  var ROOT = document.getElementById("iht-swing");
  if (!ROOT) return;

  var COLOURS = {
    "Labour": "#d50000",
    "Conservative": "#0087dc",
    "Liberal Democrat": "#faa61a",
    "Reform UK": "#12b6cf",
    "Green Party": "#6ab023",
    "Plaid Cymru": "#005b54",
    "Scottish National Party": "#fdf38e",
    "Sinn Féin": "#326760",
    "Democratic Unionist Party": "#d46a4c",
    "Social Democratic & Labour Party": "#2aa82c",
    "Ulster Unionist Party": "#48a5ee",
    "Alliance": "#f6cb2f",
    "Traditional Unionist Voice": "#0c3a6a",
    "Independent": "#8d8d8d",
    "Other": "#8d8d8d",
    "Speaker": "#8d8d8d"
  };

  // Left to right across the chamber, roughly as the benches run.
  var SEATING = [
    "Green Party", "Sinn Féin", "Social Democratic & Labour Party",
    "Plaid Cymru", "Scottish National Party", "Labour", "Alliance",
    "Independent", "Speaker", "Other", "Liberal Democrat", "Ulster Unionist Party",
    "Conservative", "Democratic Unionist Party", "Traditional Unionist Voice",
    "Reform UK"
  ];

  var SHORT = {
    "Liberal Democrat": "Lib Dem", "Scottish National Party": "SNP",
    "Green Party": "Green", "Reform UK": "Reform",
    "Democratic Unionist Party": "DUP",
    "Social Democratic & Labour Party": "SDLP",
    "Ulster Unionist Party": "UUP",
    "Traditional Unionist Voice": "TUV"
  };

  var MAJORITY = 326;
  var model = null, seats = null, baseline = null;

  function colourFor(party) { return COLOURS[party] || "#8d8d8d"; }
  function shortName(party) { return SHORT[party] || party; }

  /* ------------------------------------------------------------- the chamber */

  // One dot per seat, laid out in arcs. Seats per row are proportional to the
  // row's radius, so the dots come out evenly spaced rather than crowded on the
  // inside. Sorting by angle and then by row makes each party a solid wedge
  // instead of a scatter.
  function layout(total, rows) {
    var inner = 0.42, outer = 1.0, radii = [], i;
    for (i = 0; i < rows; i++) {
      radii.push(inner + (outer - inner) * (rows === 1 ? 0 : i / (rows - 1)));
    }
    var sum = radii.reduce(function (a, b) { return a + b; }, 0);
    var counts = radii.map(function (r) {
      return Math.max(1, Math.round(total * r / sum));
    });

    // Rounding never lands exactly on the total, so push the difference onto
    // the outer rows, which have the most room for it.
    var diff = total - counts.reduce(function (a, b) { return a + b; }, 0);
    var at = rows - 1;
    while (diff !== 0) {
      var step = diff > 0 ? 1 : -1;
      if (counts[at] + step >= 1) { counts[at] += step; diff -= step; }
      at = (at - 1 + rows) % rows;
    }

    var points = [];
    radii.forEach(function (r, row) {
      var n = counts[row];
      for (var k = 0; k < n; k++) {
        var t = n === 1 ? 0.5 : k / (n - 1);
        var angle = Math.PI - t * Math.PI;
        points.push({ x: Math.cos(angle) * r, y: -Math.sin(angle) * r,
                      t: t, row: row });
      }
    });
    points.sort(function (a, b) { return a.t - b.t || a.row - b.row; });
    return points;
  }

  var POINTS = null;

  function drawChamber(counts, effect) {
    var order = [];
    SEATING.forEach(function (party) {
      for (var i = 0; i < (counts[party] || 0); i++) order.push(party);
    });
    // Anything the seating list does not know about still has to be drawn.
    Object.keys(counts).forEach(function (party) {
      if (SEATING.indexOf(party) === -1) {
        for (var i = 0; i < counts[party]; i++) order.push(party);
      }
    });

    var total = order.length;
    if (!POINTS || POINTS.length !== total) POINTS = layout(total, 13);

    var pad = 0.06, w = 2 + pad * 2, h = 1 + pad * 2;
    var radius = 0.019;
    var parts = ['<svg viewBox="' + (-1 - pad) + ' ' + (-1 - pad) + ' ' +
                 w + ' ' + h + '" role="img" aria-label="House of Commons, ' +
                 total + ' seats">'];

    POINTS.forEach(function (p, i) {
      parts.push('<circle cx="' + p.x.toFixed(4) + '" cy="' + p.y.toFixed(4) +
                 '" r="' + radius + '" fill="' + colourFor(order[i]) + '"/>');
    });
    // The hollow in the middle of the chamber is the one place a reader is
    // already looking, so the effect of the two inheritance tax sliders goes
    // there: the seats the Conservatives hold with them, against the same
    // national swing without them.
    if (effect !== null) {
      var text = effect === 0 ? "no change"
        : (effect > 0 ? "+" : "\u2212") + Math.abs(effect) + " seat" +
          (Math.abs(effect) === 1 ? "" : "s");
      parts.push('<text class="swing-effect-label" x="0" y="-0.25" ' +
                 'text-anchor="middle">Inheritance tax</text>');
      parts.push('<text class="swing-effect-value" x="0" y="-0.135" ' +
                 'text-anchor="middle">' + text + "</text>");
    }
    parts.push("</svg>");
    ROOT.querySelector(".swing-chamber").innerHTML = parts.join("");
  }

  /* --------------------------------------------------------------- the model */

  function simulate(nationalSwing, switchShare, newVoterShare, threshold,
                    includeChildren) {
    var index = model.thresholds.indexOf(threshold);
    var counts = {};
    Object.keys(model.fixed).forEach(function (p) { counts[p] = model.fixed[p]; });

    seats.forEach(function (seat) {
      var votes = seat.v, total = 0, party;
      for (party in votes) total += votes[party];
      if (!total) return;

      // Everything that is not inheritance tax, applied first. The same number
      // of points is added to the Conservative share everywhere and taken off
      // the other parties in proportion to how each did in the seat.
      //
      // Only where the Conservatives actually stood. They did not contest the
      // Speaker's seat, and a handful of others, and adding votes for a party
      // with no candidate would be inventing a result.
      if (nationalSwing > 0 && votes.Conservative > 0) {
        var swung = {}, moved = total * nationalSwing;
        var nonCon = total - votes.Conservative;
        model.parties.forEach(function (p) {
          swung[p] = votes[p] || 0;
          if (p !== "Conservative" && nonCon > 0) {
            swung[p] = Math.max(0, swung[p] - moved * swung[p] / nonCon);
          }
        });
        swung.Conservative = (votes.Conservative || 0) + moved;
        votes = swung;
      }

      // How the seat's pensioners are assumed to have voted: the seat's own
      // result, tilted by how much better each party does among over-65s
      // nationally, then normalised back to 100%.
      // Two groups with a stake, and they do not vote alike. The pensioners
      // who would get the bill, and their adult children who stand to inherit
      // less. Two thirds of the children are between 35 and 54, where turnout
      // is around half and the Conservatives do worse than they do nationally,
      // so each group carries its own turnout and its own politics.
      var groups = [
        { people: seat.e[index], turnout: model.turnout65,
          ratio: model.pensionerRatio }
      ];
      // Off by default. Counting the children means assuming they live in
      // their parents' seat, which for many of them is simply untrue, so
      // whether to do it is the reader's call rather than ours.
      if (includeChildren) {
        groups.push({ people: seat.c[index], turnout: model.childTurnout,
                      ratio: model.childRatio });
      }

      var next = {};
      model.parties.forEach(function (p) { next[p] = votes[p] || 0; });

      groups.forEach(function (group) {
        if (!group.people) return;

        // How this group voted in the seat: the seat's own result, tilted by
        // how the group votes nationally, then normalised back to 100%.
        var share = {}, norm = 0;
        model.parties.forEach(function (p) {
          share[p] = (votes[p] || 0) / total * group.ratio[p];
          norm += share[p];
        });
        if (norm > 0) {
          model.parties.forEach(function (p) { share[p] /= norm; });
        }

        var voters = group.people * group.turnout;
        var moving = voters * (1 - share.Conservative) * switchShare;
        var otherShare = 1 - share.Conservative;

        if (otherShare > 0) {
          model.parties.forEach(function (p) {
            if (p === "Conservative") return;
            next[p] = Math.max(0, next[p] - moving * share[p] / otherShare);
          });
        }
        next.Conservative += moving;

        // Those who did not vote last time are added rather than moved, so the
        // seat's total rises.
        next.Conservative += group.people * (1 - group.turnout) * newVoterShare;
      });

      var winner = null, best = -1;
      for (party in next) {
        if (next[party] > best) { best = next[party]; winner = party; }
      }
      // "Other" is a bucket, not a party. Where it wins, the seat belongs to
      // whoever actually holds it, which is usually an independent.
      counts[winner === "Other" ? seat.w : winner] =
        (counts[winner === "Other" ? seat.w : winner] || 0) + 1;
    });

    return counts;
  }

  /* -------------------------------------------------------------- the readout */

  function largestOf(counts) {
    var best = null, n = -1;
    Object.keys(counts).forEach(function (p) {
      if (counts[p] > n) { n = counts[p]; best = p; }
    });
    return { party: best, seats: n };
  }

  function describe(counts) {
    var top = largestOf(counts);
    var con = counts.Conservative || 0;
    var line, note;

    if (top.seats >= MAJORITY) {
      line = "<strong>" + shortName(top.party) + "</strong> wins a majority of " +
             (top.seats * 2 - 650) + ", on " + top.seats + " seats.";
    } else {
      line = "<strong>" + shortName(top.party) + "</strong> is the largest party, on " +
             top.seats + " seats.";
    }

    if (top.party === "Conservative") {
      note = top.seats >= MAJORITY
        ? "The Conservatives govern alone."
        : "The Conservatives are the largest party but " +
          (MAJORITY - con) + " short of a majority.";
    } else {
      note = "The Conservatives are on " + con + ", " +
             (top.seats - con) + " behind " + shortName(top.party) +
             " and " + (MAJORITY - con) + " short of a majority.";
    }
    return line + "<em>" + note + "</em>";
  }

  function drawChips(counts) {
    var rows = Object.keys(counts).filter(function (p) { return counts[p] > 0; });
    rows.sort(function (a, b) { return counts[b] - counts[a]; });

    var html = rows.map(function (party) {
      var was = baseline[party] || 0, now = counts[party], delta = now - was;
      var move = delta === 0 ? "" :
        "<s>" + (delta > 0 ? "+" : "−") + Math.abs(delta) + "</s>";
      return '<span class="swing-chip"><i style="background:' + colourFor(party) +
             '"></i>' + shortName(party) + " <b>" + now + "</b>" + move + "</span>";
    });
    ROOT.querySelector(".swing-chips").innerHTML = html.join("");
  }

  /* ------------------------------------------------------------------- wiring */

  function read(id) { return parseInt(document.getElementById(id).value, 10); }

  function update() {
    var nationalSwing = read("swing-national") / 100;
    var switchShare = read("swing-switch") / 100;
    var newVoters = read("swing-newvoters") / 100;
    var threshold = read("swing-threshold");
    var includeChildren = document.getElementById("swing-children").checked;

    document.getElementById("swing-national-value").textContent =
      read("swing-national") + " pts";
    document.getElementById("swing-switch-value").textContent =
      read("swing-switch") + "%";
    document.getElementById("swing-newvoters-value").textContent =
      read("swing-newvoters") + "%";
    document.getElementById("swing-threshold-value").textContent =
      threshold + "%";

    var counts = simulate(nationalSwing, switchShare, newVoters, threshold,
                          includeChildren);

    // The same national swing with inheritance tax switched off, so the figure
    // in the middle of the chamber is what these two sliders are worth rather
    // than what the whole model is worth.
    var effect = null;
    if (switchShare > 0 || newVoters > 0) {
      var without = simulate(nationalSwing, 0, 0, threshold, includeChildren);
      effect = (counts.Conservative || 0) - (without.Conservative || 0);
    }

    drawChamber(counts, effect);
    drawChips(counts);
    ROOT.querySelector(".swing-result").innerHTML = describe(counts);
  }

  function wireInfoButtons() {
    ROOT.querySelectorAll(".swing-info").forEach(function (button) {
      var note = document.getElementById(button.getAttribute("aria-controls"));
      function show(on) {
        note.hidden = !on;
        button.setAttribute("aria-expanded", on ? "true" : "false");
      }
      button.addEventListener("click", function () { show(note.hidden); });
      button.addEventListener("mouseenter", function () { show(true); });
      button.addEventListener("focus", function () { show(true); });
      button.addEventListener("mouseleave", function () {
        if (button.getAttribute("data-sticky") !== "true") show(false);
      });
      // A click keeps it open; hovering away then closes it again.
      button.addEventListener("mousedown", function () {
        button.setAttribute("data-sticky", note.hidden ? "true" : "false");
      });
    });
  }

  fetch(ROOT.getAttribute("data-model")).then(function (response) {
    return response.json();
  }).then(function (data) {
    model = data;
    seats = data.seats;
    baseline = data.baseline;

    ["swing-national", "swing-switch", "swing-newvoters",
     "swing-threshold", "swing-children"].forEach(function (id) {
      document.getElementById(id).addEventListener("input", update);
      document.getElementById(id).addEventListener("change", update);
    });
    wireInfoButtons();
    update();
  }).catch(function (error) {
    ROOT.querySelector(".swing-result").textContent =
      "The model could not be loaded.";
    if (window.console) window.console.error(error);
  });
}());
