import { NavLink } from "react-router-dom";
import {
  MessagesSquare, CalendarClock, Blocks, Brain, Settings as SettingsIcon,
  PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import Runbar from "./Runbar.jsx";

/**
 * Left navigation rail. Collapsible to an icon-only rail (labels become hover
 * tooltips). On narrow screens it behaves as a slide-in drawer (driven by the
 * `drawerOpen` prop from App). The live run/queue indicator sits in the footer.
 */

// Nav items: icon + label. "Automations" is presented as "Scheduled" per request
// while the underlying route/path stays /automations (backend unchanged).
const NAV = [
  { to: "/sessions", label: "Sessions", Icon: MessagesSquare },
  { to: "/automations", label: "Scheduled", Icon: CalendarClock },
  { to: "/capabilities", label: "Skills & Tools", Icon: Blocks },
  { to: "/brain", label: "Brain", Icon: Brain },
  { to: "/settings", label: "Settings", Icon: SettingsIcon },
];

export default function Sidebar({ status, collapsed, onToggleCollapse, drawerOpen, onNavigate }) {
  const cls = "sidebar" + (collapsed ? " collapsed" : "") + (drawerOpen ? " drawer-open" : "");
  return (
    <aside className={cls}>
      <div className="sidebar-brand">
        <span className="logo" aria-hidden>
          {/* Simple claw/spark mark drawn inline so there's no extra asset to ship. */}
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
               strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3v4M12 17v4M5 12H3M21 12h-2M6.3 6.3 4.9 4.9M19.1 19.1l-1.4-1.4M17.7 6.3l1.4-1.4M4.9 19.1l1.4-1.4" />
            <circle cx="12" cy="12" r="3.2" />
          </svg>
        </span>
        <span className="brand-name">LMStudioClaw</span>
      </div>

      <nav className="sidebar-nav">
        {NAV.map(({ to, label, Icon }) => (
          <NavLink key={to} to={to} onClick={onNavigate}
                   className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="nav-ico"><Icon size={18} strokeWidth={2} /></span>
            <span className="nav-label">{label}</span>
            <span className="nav-tip">{label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-foot">
        <Runbar status={status} />
        <button className="sidebar-collapse" onClick={onToggleCollapse}
                title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen size={18} /> : <><PanelLeftClose size={18} /><span className="nav-label">Collapse</span></>}
        </button>
      </div>
    </aside>
  );
}
