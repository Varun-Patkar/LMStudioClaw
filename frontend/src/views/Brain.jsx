import { useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Brain as BrainIcon, X, Search, RefreshCw } from "lucide-react";
import { get } from "../api.js";
import Skeleton from "../components/Skeleton.jsx";

/**
 * Brain viewer — an interactive view of the agent's graph memory.
 *
 * Renders nodes + typed edges with cytoscape. Clicking a node focuses it: only that
 * node and its direct relations stay highlighted (everything else fades) and a sidebar
 * opens with the node's Markdown details and its connections. Node/edge type filters
 * and a text search make a large brain easy to explore.
 *
 * Read-only: the agent owns writes via its tools; this page only inspects.
 */

// A small, theme-agnostic palette assigned to node types in discovery order so each
// type gets a stable, distinct color across renders.
const PALETTE = [
  "#6aa0ff", "#48c78e", "#f6b73c", "#ff6b81", "#a78bfa",
  "#22c7c7", "#ff924c", "#c9d34b", "#e879f9", "#7dd3fc",
];

export default function Brain() {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const [meta, setMeta] = useState(null);            // {node_types, edge_types, counts}
  const [graph, setGraph] = useState(null);          // {nodes, edges}
  const [nodeFilter, setNodeFilter] = useState({});  // type -> bool
  const [edgeFilter, setEdgeFilter] = useState({});  // type -> bool
  const [selected, setSelected] = useState(null);    // {node, details, edges, neighbors}
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);

  // Stable color-by-type map derived from the discovered node types.
  const colorOf = useMemo(() => {
    const map = {};
    (meta?.node_types || []).forEach((t, i) => { map[t] = PALETTE[i % PALETTE.length]; });
    return map;
  }, [meta]);

  // -- data loading ----------------------------------------------------------
  async function loadMeta() {
    const m = await get("/api/brain/meta");
    setMeta(m);
    setNodeFilter((prev) => Object.fromEntries(m.node_types.map((t) => [t, prev[t] ?? true])));
    setEdgeFilter((prev) => Object.fromEntries(m.edge_types.map((t) => [t, prev[t] ?? true])));
    return m;
  }

  async function loadGraph(nf = nodeFilter, ef = edgeFilter) {
    const params = new URLSearchParams();
    const nodes = Object.entries(nf).filter(([, on]) => on).map(([t]) => t);
    const edges = Object.entries(ef).filter(([, on]) => on).map(([t]) => t);
    // Only send a filter when the user has actually narrowed it (keeps URLs clean).
    if (meta && nodes.length && nodes.length < meta.node_types.length) params.set("node_types", nodes.join(","));
    if (meta && edges.length && edges.length < meta.edge_types.length) params.set("edge_types", edges.join(","));
    const g = await get("/api/brain/graph" + (params.toString() ? `?${params}` : ""));
    setGraph(g);
  }

  useEffect(() => {
    (async () => {
      setLoading(true);
      try { await loadMeta(); await loadGraph(); } catch { /* empty brain still renders */ }
      finally { setLoading(false); }
    })();
    // eslint-disable-next-line
  }, []);

  // Re-query the graph whenever filters change (after the initial load).
  useEffect(() => {
    if (!meta) return;
    loadGraph().catch(() => {});
    // eslint-disable-next-line
  }, [nodeFilter, edgeFilter]);

  // -- cytoscape render ------------------------------------------------------
  useEffect(() => {
    if (!graph || !containerRef.current) return;
    const css = getComputedStyle(document.documentElement);
    const fg = (css.getPropertyValue("--fg") || "#e6e6e6").trim() || "#e6e6e6";
    // Outline colour for labels so text stays readable over a dense, overlapping graph.
    const bg = (css.getPropertyValue("--bg") || "#0f1115").trim() || "#0f1115";
    const elements = [
      ...graph.nodes.map((n) => ({
        data: { id: n.id, label: n.label, type: n.type, color: colorOf[n.type] || "#8aa",
                deg: 0 },
      })),
      ...graph.edges.map((e) => ({
        data: { id: e.id, source: e.source, target: e.target, label: e.type },
      })),
    ];
    // Pre-compute degree so well-connected nodes (hubs) render larger — a common,
    // readable convention for force-directed graphs.
    const degree = {};
    for (const e of graph.edges) {
      degree[e.source] = (degree[e.source] || 0) + 1;
      degree[e.target] = (degree[e.target] || 0) + 1;
    }
    for (const el of elements) {
      if (el.data && el.data.label !== undefined && el.data.source === undefined) {
        el.data.deg = degree[el.data.id] || 0;
      }
    }
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        { selector: "node", style: {
          "background-color": "data(color)", label: "data(label)",
          color: fg, "font-size": 11, "text-wrap": "wrap",
          "text-max-width": 120, "text-valign": "bottom", "text-margin-y": 5,
          // Scale node size with degree so hubs stand out (22px..46px).
          width: "mapData(deg, 0, 12, 22, 46)", height: "mapData(deg, 0, 12, 22, 46)",
          "border-width": 2, "border-color": "rgba(255,255,255,.18)",
          "text-outline-width": 3, "text-outline-color": bg, "text-outline-opacity": 0.9,
          "transition-property": "opacity, width, height, border-color, border-width",
          "transition-duration": "120ms",
        } },
        { selector: "edge", style: {
          width: 1.5, "line-color": "rgba(140,150,170,.40)",
          "target-arrow-color": "rgba(140,150,170,.55)", "target-arrow-shape": "triangle",
          "arrow-scale": 0.9, "curve-style": "bezier", label: "data(label)", "font-size": 8,
          color: "rgba(150,160,180,.85)", "text-rotation": "autorotate",
          "text-outline-width": 2, "text-outline-color": bg, "text-outline-opacity": 0.7,
        } },
        { selector: ".faded", style: { opacity: 0.10, "text-opacity": 0.06 } },
        { selector: ".focus", style: {
          "border-color": "#fff", "border-width": 3,
        } },
        { selector: "node:active", style: { "overlay-opacity": 0.1 } },
      ],
      // Animated force-directed layout (cose): nodes repel, edges act as springs, and
      // ``nodeDimensionsIncludeLabels`` keeps labels from overlapping. The graph visibly
      // settles into place, and nodes can be dragged to re-tug their neighbours' edges.
      layout: {
        name: "cose", animate: true, animationDuration: 800, randomize: true,
        fit: true, padding: 50, nodeDimensionsIncludeLabels: true,
        nodeRepulsion: 22000, idealEdgeLength: 130, edgeElasticity: 110,
        nestingFactor: 1.2, gravity: 0.3, numIter: 2000, nodeOverlap: 24,
        componentSpacing: 140, coolingFactor: 0.95, initialTemp: 220,
      },
      minZoom: 0.15, maxZoom: 3,
      wheelSensitivity: 0.2,
    });
    cyRef.current = cy;

    cy.on("tap", "node", (evt) => selectNode(evt.target.id()));
    cy.on("tap", (evt) => { if (evt.target === cy) clearFocus(); });

    return () => { cy.destroy(); cyRef.current = null; };
    // eslint-disable-next-line
  }, [graph, colorOf]);

  // -- focus / selection -----------------------------------------------------
  function applyFocus(id) {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("focus");
    if (!id) { cy.elements().removeClass("faded"); return; }
    const node = cy.$id(id);
    if (node.empty()) return;
    const keep = node.closedNeighborhood();
    cy.elements().addClass("faded");
    keep.removeClass("faded");
    node.addClass("focus");
  }

  async function selectNode(id) {
    applyFocus(id);
    try { setSelected(await get(`/api/brain/node/${encodeURIComponent(id)}`)); }
    catch { setSelected(null); }
  }

  function clearFocus() {
    applyFocus(null);
    setSelected(null);
  }

  /** Re-run the force-directed layout on the current graph (no data reload). */
  function relayout() {
    const cy = cyRef.current;
    if (!cy) return;
    cy.layout({
      name: "cose", animate: true, animationDuration: 800, randomize: true,
      fit: true, padding: 50, nodeDimensionsIncludeLabels: true,
      nodeRepulsion: 22000, idealEdgeLength: 130, edgeElasticity: 110,
      gravity: 0.3, numIter: 2000, nodeOverlap: 24, componentSpacing: 140,
    }).run();
  }

  function runSearch(e) {
    e?.preventDefault();
    const cy = cyRef.current;
    if (!cy || !query.trim()) return;
    const q = query.trim().toLowerCase();
    const hit = cy.nodes().filter((n) => (n.data("label") || "").toLowerCase().includes(q));
    if (hit.nonempty()) {
      const first = hit[0];
      cy.animate({ center: { eles: first }, zoom: 1.4 }, { duration: 250 });
      selectNode(first.id());
    }
  }

  const isEmpty = graph && graph.nodes.length === 0;

  return (
    <>
      <div className="view-head">
        <h1>Brain</h1>
        <span className="sub">The agent&apos;s graph memory — nodes, relationships &amp; details</span>
      </div>

      {loading ? <Skeleton /> : (
        <div className="brain-wrap">
          {/* Controls: search + node/edge type filters */}
          <div className="brain-controls">
            <form className="brain-search" onSubmit={runSearch}>
              <Search size={15} />
              <input value={query} placeholder="Find a node…"
                     onChange={(e) => setQuery(e.target.value)} />
            </form>
            <button className="btn ghost sm" onClick={() => { loadMeta(); loadGraph(); clearFocus(); }}>
              <RefreshCw size={14} /> Refresh
            </button>
            <button className="btn ghost sm" onClick={relayout} title="Re-run the force-directed layout">
              <RefreshCw size={14} /> Re-layout
            </button>
            <FilterGroup title="Node types" filter={nodeFilter} setFilter={setNodeFilter} colors={colorOf} />
            <FilterGroup title="Edge types" filter={edgeFilter} setFilter={setEdgeFilter} />
          </div>

          {/* Graph canvas + slide-in detail sidebar */}
          <div className="brain-stage">
            <div ref={containerRef} className="brain-canvas" />
            {isEmpty && (
              <div className="brain-empty">
                <BrainIcon size={34} />
                <p>Your brain is empty.</p>
                <span>As the agent works, it records people, projects, facts and how they
                  relate here. Come back after a few sessions.</span>
              </div>
            )}
            {selected?.node && (
              <aside className="brain-side">
                <div className="brain-side-head">
                  <span className="brain-chip" style={{ background: colorOf[selected.node.type] || "#8aa" }} />
                  <div>
                    <h3>{selected.node.label}</h3>
                    <span className="brain-type">{selected.node.type}</span>
                  </div>
                  <button className="icon-btn" onClick={clearFocus} aria-label="Close"><X size={16} /></button>
                </div>
                {selected.node.summary && <p className="brain-summary">{selected.node.summary}</p>}
                <div className="brain-detail md">
                  {selected.details?.trim()
                    ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{selected.details}</ReactMarkdown>
                    : <span className="muted">No detailed notes for this node.</span>}
                </div>
                {selected.edges?.length > 0 && (
                  <div className="brain-rels">
                    <h4>Connections</h4>
                    {selected.edges.map((e) => {
                      const otherId = e.source === selected.node.id ? e.target : e.source;
                      const other = (selected.neighbors || []).find((n) => n.id === otherId);
                      const out = e.source === selected.node.id;
                      return (
                        <button key={e.id} className="brain-rel" onClick={() => selectNode(otherId)}>
                          <span className="brain-rel-type">{out ? "→" : "←"} {e.type}</span>
                          <span className="brain-rel-name">{other?.label || otherId}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </aside>
            )}
          </div>
        </div>
      )}
    </>
  );
}

/** A collapsible row of type checkboxes used for both node and edge filtering. */
function FilterGroup({ title, filter, setFilter, colors }) {
  const types = Object.keys(filter);
  if (types.length === 0) return null;
  const toggle = (t) => setFilter((f) => ({ ...f, [t]: !f[t] }));
  return (
    <div className="brain-filter">
      <span className="brain-filter-title">{title}</span>
      <div className="brain-filter-chips">
        {types.map((t) => (
          <button key={t} className={"brain-tag" + (filter[t] ? " on" : "")} onClick={() => toggle(t)}>
            {colors && <span className="dot" style={{ background: colors[t] || "#8aa" }} />}
            {t}
          </button>
        ))}
      </div>
    </div>
  );
}
