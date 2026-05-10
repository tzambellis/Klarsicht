import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Overview from "./pages/Overview";
import IncidentList from "./pages/IncidentList";
import IncidentDetail from "./pages/IncidentDetail";
import Investigate from "./pages/Investigate";
import Setup from "./pages/Setup";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import Admin from "./pages/Admin";
import { AuthProvider, useAuth } from "./auth";

function Nav() {
  const { enabled, user, isAdmin, logout } = useAuth();
  const link = "text-sm transition-colors";
  const active = "text-white";
  const inactive = "text-[#888] hover:text-white";

  return (
    <header className="border-b border-white/[0.08] sticky top-0 z-50 bg-black/80 backdrop-blur-md">
      <div className="mx-auto max-w-5xl flex items-center justify-between px-6 h-14">
        <NavLink to="/" className="flex items-center gap-2.5">
          <div className="h-6 w-6 rounded bg-white flex items-center justify-center">
            <span className="text-black text-xs font-bold leading-none">K</span>
          </div>
          <span className="text-sm font-semibold tracking-tight">Klarsicht</span>
        </NavLink>
        <nav className="flex items-center gap-6">
          <NavLink to="/" end className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>
            Overview
          </NavLink>
          <NavLink to="/incidents" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>
            Incidents
          </NavLink>
          <NavLink to="/investigate" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>
            Investigate
          </NavLink>
          <NavLink to="/setup" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>
            Setup
          </NavLink>
          {isAdmin && (
            <NavLink to="/admin" className={({ isActive }) => `${link} ${isActive ? active : inactive}`}>
              Admin
            </NavLink>
          )}
          {enabled && user && (
            <button onClick={logout} className={`${link} ${inactive}`}>
              Sign out
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}

function ProtectedApp() {
  const { enabled, user, isAdmin, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-black text-white">
        <p className="text-sm text-[#888]">Loading…</p>
      </div>
    );
  }

  if (enabled && !user) {
    return <Login />;
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <Nav />
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/incidents" element={<IncidentList />} />
        <Route path="/incidents/:id" element={<IncidentDetail />} />
        <Route path="/investigate" element={<Investigate />} />
        <Route path="/setup" element={<Setup />} />
        {isAdmin && <Route path="/admin" element={<Admin />} />}
        <Route path="/oauth2/callback" element={<Overview />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
      <div className="fixed bottom-3 left-4 text-[10px] font-mono text-[#555] pointer-events-none select-none">
        v{__APP_VERSION__}
      </div>
    </div>
  );
}

// Detect basename from URL — supports both /app/ (with landing) and / (no landing)
const BASENAME = window.location.pathname.startsWith("/app") ? "/app" : "/";

export default function App() {
  return (
    <AuthProvider basename={BASENAME}>
      <BrowserRouter basename={BASENAME}>
        <ProtectedApp />
      </BrowserRouter>
    </AuthProvider>
  );
}
