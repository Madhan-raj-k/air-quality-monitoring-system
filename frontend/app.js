const app = document.querySelector("#app");

function sentence(text) {
  if (!text) return "";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function clock(value) {
  const [date, time] = value.split(" ");
  const [year, month, day] = date.split("-").map(Number);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${day} ${months[month - 1]} ${year}, ${time}`;
}

function hourLabel(value) {
  return value.slice(11, 16);
}

function chart(series) {
  const width = 760;
  const height = 250;
  const pad = { left: 42, right: 12, top: 16, bottom: 32 };
  const known = series.map((point) => point.aqi).filter((value) => value != null);
  if (!known.length) {
    return "<p class='note'>No official hours in this window.</p>";
  }
  let min = Math.min(...known);
  let max = Math.max(...known);
  if (min === max) {
    min -= 5;
    max += 5;
  }
  const slack = (max - min) * 0.18;
  min -= slack;
  max += slack;
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const xAt = (index) => pad.left + (series.length === 1 ? innerW / 2 : (index / (series.length - 1)) * innerW);
  const yAt = (value) => pad.top + ((max - value) / (max - min)) * innerH;

  const ticks = [0, 1, 2, 3].map((step) => min + ((max - min) * step) / 3);
  const grid = ticks.map((tick) => {
    const y = yAt(tick);
    return `<line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" stroke="#e3ebe6" />
      <text x="${pad.left - 8}" y="${y + 4}" text-anchor="end" fill="#5d6d64" font-size="11">${Math.round(tick)}</text>`;
  }).join("");

  let path = "";
  let penDown = false;
  series.forEach((point, index) => {
    if (point.aqi == null) {
      penDown = false;
      return;
    }
    path += `${penDown ? "L" : "M"} ${xAt(index).toFixed(1)} ${yAt(point.aqi).toFixed(1)} `;
    penDown = true;
  });

  const dots = series.map((point, index) => {
    if (point.aqi == null) return "";
    return `<circle cx="${xAt(index).toFixed(1)}" cy="${yAt(point.aqi).toFixed(1)}" r="2.4" fill="#10281f" />`;
  }).join("");

  const labelIndexes = [0, Math.floor((series.length - 1) / 2), series.length - 1];
  const labels = [...new Set(labelIndexes)].map((index) => {
    return `<text x="${xAt(index)}" y="${height - 8}" text-anchor="middle" fill="#5d6d64" font-size="11">${hourLabel(series[index].datetime)}</text>`;
  }).join("");

  return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Official AQI for the last 24 hours">
    ${grid}
    <path d="${path}" fill="none" stroke="#10281f" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round" />
    ${dots}
    ${labels}
  </svg>`;
}

function pill(reading) {
  const darkText = reading.category === "Satisfactory" || reading.category === "Moderate";
  const text = darkText ? "#1c2412" : "#ffffff";
  return `<span class="pill" style="background:${reading.color};color:${text}">${reading.category}</span>`;
}

function render(data) {
  const current = data.current;
  const forecast = data.forecast;
  const summary = data.last_24_summary;
  const active = new Set([current.category, forecast && forecast.category].filter(Boolean));
  const rows = data.bands.map((band) => {
    const mark = active.has(band.category) ? " class='active'" : "";
    return `<tr${mark}>
      <td class="band-range">${band.range}</td>
      <td><span class="swatch" style="background:${band.color}"></span>${band.category}</td>
      <td>${sentence(band.advice)}</td>
    </tr>`;
  }).join("");

  const forecastBlock = forecast
    ? `<p class="aqi">${forecast.aqi}</p>
       ${pill(forecast)}
       <p class="when">For ${clock(forecast.datetime)}</p>
       <p class="advice">${sentence(forecast.advice)}</p>`
    : `<p class="note">${data.forecast_note || "Forecast unavailable."}</p>`;

  app.innerHTML = `
    <header class="masthead">
      <div class="brand">
        <div class="mark" aria-hidden="true"></div>
        <div>
          <h1>Air Quality Monitoring System</h1>
          <p class="place">${data.station.name}, ${data.station.place}</p>
        </div>
      </div>
      <div class="mast-meta">
        <span class="chip">${data.station.agency}</span>
        <span class="chip">${data.station.site_id}</span>
        <span class="chip">${data.station.source}</span>
        <button class="print-btn" type="button">Print report</button>
      </div>
    </header>
    <main class="layout">
      <section class="card current" data-category="${current.category}">
        <div>
          <p class="kicker">Current reading</p>
          <p class="aqi">${current.aqi}</p>
          <p class="category">${current.category}</p>
        </div>
        <div>
          <p class="when">${clock(current.datetime)}</p>
          <p class="when">${escapeHtml(current.source || "")}</p>
          <p class="advice">${sentence(current.advice)}</p>
        </div>
      </section>
      <section class="card chart-card">
        <div class="section-head">
          <h2>Last 24 hours</h2>
          <p>${summary.hours_missing ? `${summary.hours_missing} hour${summary.hours_missing === 1 ? "" : "s"} missing` : "Hourly series"}</p>
        </div>
        ${summary.detail ? `<p class="series-note">${escapeHtml(summary.detail)}</p>` : ""}
        ${chart(data.last_24_hours)}
        <div class="stats">
          <div class="stat"><b>${summary.minimum ?? "–"}</b><span>Lowest</span></div>
          <div class="stat"><b>${summary.average ?? "–"}</b><span>Average</span></div>
          <div class="stat"><b>${summary.maximum ?? "–"}</b><span>Highest</span></div>
        </div>
      </section>
      <section class="card forecast">
        <p class="kicker">6-hour forecast</p>
        ${forecastBlock}
      </section>
      <section class="card guidance">
        <div class="section-head">
          <h2>Health guidance</h2>
          <p>Assigned from the AQI category</p>
        </div>
        <table>
          <thead><tr><th>AQI</th><th>Category</th><th>Guidance</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </section>
    </main>`;

  app.querySelector(".print-btn").addEventListener("click", () => window.print());
}

fetch("/api/report")
  .then((response) => response.json().then((body) => {
    if (!response.ok) throw new Error(body.error || "The report could not be loaded.");
    return body;
  }))
  .then(render)
  .catch((error) => {
    app.innerHTML = `<p class="status-line">${error.message}</p>`;
  });
