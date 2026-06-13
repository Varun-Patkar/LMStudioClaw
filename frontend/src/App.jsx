import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { get, useStatusSocket } from "./api.js";
import Runbar from "./components/Runbar.jsx";
import Sessions from "./views/Sessions.jsx";
import SessionDetail from "./views/SessionDetail.jsx";
import Automations from "./views/Automations.jsx";
import Capabilities from "./views/Capabilities.jsx";
import Settings from "./views/Settings.jsx";

const NAV = [
  { to: "/sessions", label: "Sessions" },
  { to: "/automations", label: "Automations" },
  { to: "/capabilities", label: "Skills & Tools" },
  { to: "/settings", label: "Settings" },
];

/** Animated wrapper so each routed page eases in/out. */
function Page({ children }) {
  return (
    <motion.div
      className="view"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
    >
      {children}
    </motion.div>
  );
}

export default function App() {
  const status = useStatusSocket();
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);

  // Apply the saved theme once on load.
  useEffect(() => {
    get("/api/settings")
      .then((s) => document.documentElement.setAttribute("data-theme", s.theme || "system"))
      .catch(() => document.documentElement.setAttribute("data-theme", "system"));
  }, []);

  // Close the mobile nav whenever the route changes.
  useEffect(() => setNavOpen(false), [location.pathname]);

  return (
    <>
      <header className="topbar">
        <h1>LMStudioClaw</h1>
        <button className="nav-toggle" onClick={() => setNavOpen((o) => !o)} aria-label="Menu">☰</button>
        <nav className={"nav" + (navOpen ? " open" : "")}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <span className="spacer" />
        <Runbar status={status} />
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
    </>
  );
}
