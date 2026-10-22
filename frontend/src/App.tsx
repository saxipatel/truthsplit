import { lazy, Suspense } from "react";
import { Link, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import { EmptyState, Loading } from "./components/ui";
import Overview from "./pages/Overview";

// Pages load on first visit; charts (Recharts) are only fetched by the pages that draw them.
const History = lazy(() => import("./pages/History"));
const InvestigationDetail = lazy(() => import("./pages/InvestigationDetail"));
const Metrics = lazy(() => import("./pages/Metrics"));
const NewInvestigation = lazy(() => import("./pages/NewInvestigation"));
const Settings = lazy(() => import("./pages/Settings"));
const Sources = lazy(() => import("./pages/Sources"));

function NotFound() {
  return (
    <EmptyState title="Page not found">
      <Link to="/" className="text-blue-800 hover:underline">
        Go to the overview
      </Link>
    </EmptyState>
  );
}

export default function App() {
  return (
    <Suspense fallback={<Loading label="Loading page" />}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="investigations/new" element={<NewInvestigation />} />
          <Route path="investigations/:id" element={<InvestigationDetail />} />
          <Route path="sources" element={<Sources />} />
          <Route path="metrics" element={<Metrics />} />
          <Route path="history" element={<History />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
