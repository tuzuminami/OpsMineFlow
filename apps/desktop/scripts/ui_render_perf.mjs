import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

const totalEvents = Number.parseInt(process.env.OPSMINEFLOW_PERF_TOTAL_EVENTS ?? "100000", 10);
const displayedEvents = 500;
const maximumRenderSeconds = 1;

if (!Number.isSafeInteger(totalEvents) || totalEvents < displayedEvents) {
  throw new Error("OPSMINEFLOW_PERF_TOTAL_EVENTS must be an integer of at least 500.");
}

globalThis.window = {
  localStorage: { getItem: () => null, setItem: () => undefined },
  navigator: { language: "en-US" }
};

const vite = await createServer({
  root: new URL("..", import.meta.url).pathname,
  server: { middlewareMode: true },
  appType: "custom"
});

try {
  const { EventsView } = await vite.ssrLoadModule("/src/App.tsx");
  const { I18nProvider } = await vite.ssrLoadModule("/src/i18n.tsx");
  const events = Array.from({ length: displayedEvents }, (_, index) => ({
    event_id: `render-${index}`,
    case_id: `CASE-${Math.floor(index / 10)}`,
    activity_raw: `Activity ${index % 20}`,
    app_name: `App ${index % 8}`,
    window_title_masked: "Masked window",
    domain: "",
    duration_seconds: 30,
    confidential_flag: false
  }));
  const started = performance.now();
  const html = renderToStaticMarkup(
    React.createElement(
      I18nProvider,
      null,
      React.createElement(EventsView, {
        events,
        total: totalEvents,
        onLoadMore: async () => undefined,
        working: false
      })
    )
  );
  const renderSeconds = (performance.now() - started) / 1000;
  const rowCount = (html.match(/<tr/g) ?? []).length - 1;

  assert.equal(rowCount, displayedEvents, "the bounded event page must render every supplied row");
  assert.match(html, /Load more events/, "the remaining event count must be discoverable without rendering all rows");
  assert.ok(
    renderSeconds <= maximumRenderSeconds,
    `rendering ${displayedEvents} rows exceeded ${maximumRenderSeconds} second(s): ${renderSeconds.toFixed(3)}`
  );
  console.log(JSON.stringify({
    ui_render_smoke: {
      total_events: totalEvents,
      displayed_events: displayedEvents,
      render_seconds: Number(renderSeconds.toFixed(3)),
      maximum_render_seconds: maximumRenderSeconds
    }
  }));
} finally {
  await vite.close();
}
