import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import Overview from "./pages/Overview";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
      </Route>
    </Routes>
  );
}
