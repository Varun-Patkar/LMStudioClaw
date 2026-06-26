import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { Menu } from "lucide-react";
import { get, useStatusSocket } from "./api.js";
import { StatusContext } from "./lib/status.js";
import Sidebar from "./components/Sidebar.jsx";
import Sessions from "./views/Sessions.jsx";
import SessionDetail from "./views/SessionDetail.jsx";
import Automations from "./views/Automations.jsx";
import Capabilities from "./views/Capabilities.jsx";
import Settings from "./views/Settings.jsx";

/** Animated wrapper so each routed page eases in (kept short so it never feels laggy). */
function Page({ children }) {
  return (
    <motion.div
      className="view"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={{ duration: 0.14, ease: [0.4, 0, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}

export default function App() {
  const status = useStatusSocket();
  const location = useLocation();
  // Persist the collapsed preference so the rail keeps its width across reloads.
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar.collapsed") === "1");
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Apply the saved theme once on load.
  useEffect(() => {
    get("/api/settings")
      .then((s) => document.documentElement.setAttribute("data-theme", s.theme || "system"))
      .catch(() => document.documentElement.setAttribute("data-theme", "system"));
  }, []);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  function toggleCollapse() {
    setCollapsed((c) => { localStorage.setItem("sidebar.collapsed", c ? "0" : "1"); return !c; });
  }

  return (
    <StatusContext.Provider value={status}>
      <div className="app-shell">
        <div className={"sidebar-scrim" + (drawerOpen ? " show" : "")} onClick={() => setDrawerOpen(false)} />
        <Sidebar status={status} collapsed={collapsed} onToggleCollapse={toggleCollapse}
                 drawerOpen={drawerOpen} onNavigate={() => setDrawerOpen(false)} />

        <div className="main">
          <header className="mobile-bar">
            <button className="menu-btn" aria-label="Menu" onClick={() => setDrawerOpen((o) => !o)}>
              <Menu size={20} />
            </button>
            <span className="brand-name">LMStudioClaw</span>
          </header>

          <AnimatePresence mode="wait">
            <Routes location={location} key={location.pathname.split("/").slice(0, 2).join("/")}>
              <Route path="/" element={<Navigate to="/sessions" replace />} />
              <Route path="/sessions" element={<Page><Sessions /></Page>} />
              <Route path="/sessions/:id" element={<Page><SessionDetail /></Page>} />
              <Route path="/automations" element={<Page><Automations /></Page>} />
              <Route path="/capabilities" element={<Page><Capabilities /></Page>} />
              <Route path="/settings" element={<Page><Settings /></Page>} />
            </Routes>
          </AnimatePresence>
        </div>
      </div>
    </StatusContext.Provider>
  );
}
