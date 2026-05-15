import { useMemo, useState } from "react";
import {
  captureBacklog,
  captureStateLabels,
  screens,
  type WalkthroughScreen
} from "./walkthroughData";

function groupByArea(items: WalkthroughScreen[]) {
  return items.reduce<Record<string, WalkthroughScreen[]>>((groups, screen) => {
    groups[screen.area] = groups[screen.area] ?? [];
    groups[screen.area].push(screen);
    return groups;
  }, {});
}

function Pill({ children, tone = "neutral" }: { children: string; tone?: "neutral" | "good" | "warn" }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function NoteList({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="note-block">
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

function App() {
  const [activeId, setActiveId] = useState(screens[0].id);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"all" | "refresh">("all");
  const [imageMode, setImageMode] = useState<"fit" | "inspect">("fit");
  const [zoom, setZoom] = useState(1);
  const [imageSizes, setImageSizes] = useState<Record<string, { width: number; height: number }>>({});

  const filteredScreens = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return screens.filter((screen) => {
      const matchesMode = mode === "all" || screen.captureState === "needs-refresh";
      const haystack = [
        screen.area,
        screen.title,
        screen.route,
        screen.summary,
        ...screen.subsections,
        ...screen.operatorFocus
      ]
        .join(" ")
        .toLowerCase();
      return matchesMode && (!normalized || haystack.includes(normalized));
    });
  }, [mode, query]);

  const grouped = useMemo(() => groupByArea(filteredScreens), [filteredScreens]);
  const active = screens.find((screen) => screen.id === activeId) ?? screens[0];
  const captureTone = active.captureState === "current" ? "good" : active.captureState === "needs-refresh" ? "warn" : "neutral";
  const activeImageSize = imageSizes[active.image];
  const inspectWidth = activeImageSize ? Math.round(activeImageSize.width * zoom) : undefined;
  const zoomStops = [1, 1.5, 2, 2.5];

  function selectScreen(id: string) {
    setActiveId(id);
    setImageMode("fit");
    setZoom(1);
  }

  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Walkthrough screens">
        <div className="brand">
          <span className="brand-mark">L</span>
          <div>
            <p className="eyebrow">Standalone guide</p>
            <h1>War Room Visual Walkthrough</h1>
          </div>
        </div>

        <div className="search-panel">
          <label htmlFor="screen-search">Find a screen</label>
          <input
            id="screen-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search tabs, controls, receipts"
          />
          <div className="segmented" aria-label="Capture filter">
            <button className={mode === "all" ? "active" : ""} onClick={() => setMode("all")}>
              All
            </button>
            <button className={mode === "refresh" ? "active" : ""} onClick={() => setMode("refresh")}>
              Refresh queue
            </button>
          </div>
        </div>

        <nav className="screen-nav">
          {Object.entries(grouped).map(([area, areaScreens]) => (
            <section key={area}>
              <h2>{area}</h2>
              {areaScreens.map((screen) => (
                <button
                  key={screen.id}
                  className={screen.id === active.id ? "selected" : ""}
                  onClick={() => selectScreen(screen.id)}
                >
                  <span>{screen.title}</span>
                  {screen.captureState === "needs-refresh" && <small>refresh</small>}
                </button>
              ))}
            </section>
          ))}
        </nav>
      </aside>

      <section className="content">
        <nav className="mobile-navigator" aria-label="Mobile walkthrough navigation">
          <div className="mobile-nav-header">
            <div>
              <p className="eyebrow">Standalone guide</p>
              <strong>War Room Visual Walkthrough</strong>
            </div>
            <Pill tone={captureTone}>{active.area}</Pill>
          </div>

          <div className="mobile-controls">
            <label htmlFor="mobile-screen-search">Find a screen</label>
            <input
              id="mobile-screen-search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search screens"
            />
            <div className="segmented" aria-label="Mobile capture filter">
              <button className={mode === "all" ? "active" : ""} onClick={() => setMode("all")}>
                All
              </button>
              <button className={mode === "refresh" ? "active" : ""} onClick={() => setMode("refresh")}>
                Refresh
              </button>
            </div>
            <label htmlFor="mobile-screen-select">Current screen</label>
            <select
              id="mobile-screen-select"
              value={active.id}
              onChange={(event) => selectScreen(event.target.value)}
            >
              {Object.entries(grouped).map(([area, areaScreens]) => (
                <optgroup key={area} label={area}>
                  {areaScreens.map((screen) => (
                    <option key={screen.id} value={screen.id}>
                      {screen.title}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        </nav>

        <header className="hero">
          <div>
            <p className="eyebrow">Operator manual asset</p>
            <h2>War Room screen library for operator evaluation</h2>
            <p>
              A navigable, read-only React guide modeled after War Room's operational surface. It uses
              full-page screenshot assets and explains what each screen proves, what the operator can do,
              and where governance or receipt evidence should appear.
            </p>
          </div>
          <dl className="metrics">
            <div>
              <dt>{screens.length}</dt>
              <dd>screens mapped</dd>
            </div>
            <div>
              <dt>{new Set(screens.map((screen) => screen.area)).size}</dt>
              <dd>areas</dd>
            </div>
            <div>
              <dt>{captureBacklog.length}</dt>
              <dd>refresh candidates</dd>
            </div>
          </dl>
        </header>

        <article className="walkthrough-card">
          <div className="screen-header">
            <div>
              <p className="eyebrow">{active.area}</p>
              <h2>{active.title}</h2>
              <code>{active.route}</code>
            </div>
            <Pill tone={captureTone}>{captureStateLabels[active.captureState]}</Pill>
          </div>

          <div className={`screen-layout ${imageMode === "inspect" ? "screen-layout-inspect" : ""}`}>
            <figure className="screenshot-frame">
              <div className="screenshot-toolbar" aria-label="Screenshot view controls">
                <div className="segmented image-toggle">
                  <button className={imageMode === "fit" ? "active" : ""} onClick={() => setImageMode("fit")}>
                    Fit
                  </button>
                  <button
                    className={imageMode === "inspect" ? "active" : ""}
                    onClick={() => setImageMode("inspect")}
                  >
                    Inspect
                  </button>
                </div>
                <div className="zoom-controls">
                  {zoomStops.map((value) => (
                    <button
                      key={value}
                      className={imageMode === "inspect" && zoom === value ? "active" : ""}
                      onClick={() => {
                        setImageMode("inspect");
                        setZoom(value);
                      }}
                    >
                      {Math.round(value * 100)}%
                    </button>
                  ))}
                  <a href={active.image} target="_blank" rel="noreferrer">
                    Original
                  </a>
                </div>
              </div>
              <div className="image-viewport">
                <img
                  src={active.image}
                  alt={`${active.title} screenshot`}
                  onLoad={(event) => {
                    const image = event.currentTarget;
                    setImageSizes((current) => ({
                      ...current,
                      [active.image]: {
                        width: image.naturalWidth,
                        height: image.naturalHeight
                      }
                    }));
                  }}
                  style={imageMode === "inspect" && inspectWidth ? { width: `${inspectWidth}px` } : undefined}
                />
              </div>
              <figcaption>
                {active.captureState === "needs-refresh"
                  ? "Representative stitched capture pending a completed demo state"
                  : imageMode === "inspect"
                    ? "Scrollable original-resolution inspection view"
                    : "Fit-to-page War Room capture"}
              </figcaption>
            </figure>

            <aside className="screen-brief">
              <h3>Operator Read</h3>
              <p>{active.summary}</p>
              <div className="subsection-list">
                {active.subsections.map((section) => (
                  <span key={section}>{section}</span>
                ))}
              </div>
              <div className="next-step">
                <strong>Next step</strong>
                <p>{active.nextStep}</p>
              </div>
            </aside>
          </div>

          <div className="notes-grid">
            <NoteList title="What To Inspect" items={active.operatorFocus} />
            <NoteList title="Governance Surface" items={active.governance} />
            <NoteList title="Receipt Evidence" items={active.receipts} />
            <section className="note-block degraded">
              <h3>Degraded State</h3>
              <p>{active.degradedState}</p>
            </section>
          </div>
        </article>

        <section className="capture-queue">
          <div>
            <p className="eyebrow">Capture backlog</p>
            <h2>Fresh screenshots to add next</h2>
          </div>
          <div className="queue-list">
            {captureBacklog.map((screen) => (
              <button key={screen.id} onClick={() => selectScreen(screen.id)}>
                <span>{screen.title}</span>
                <small>{screen.route}</small>
              </button>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}

export default App;
