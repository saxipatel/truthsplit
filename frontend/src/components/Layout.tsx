import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/investigations/new", label: "New investigation" },
  { to: "/sources", label: "Sources" },
  { to: "/metrics", label: "Metric definitions" },
  { to: "/history", label: "History" },
  { to: "/settings", label: "Settings" },
];

export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <NavLink to="/" className="font-semibold tracking-tight">
            TruthSplit
          </NavLink>
          <nav aria-label="Main" className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? "font-medium text-slate-900 underline underline-offset-4" : "text-slate-500 hover:text-slate-900")}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
