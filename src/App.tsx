import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Layout";
import { Spinner } from "./components/ui";
import { useBrands } from "./lib/hooks";
import Overview from "./features/Overview";
import Sales from "./features/Sales";
import Marketing from "./features/Marketing";
import Creators from "./features/Creators";
import Voice from "./features/Voice";
import Web from "./features/Web";
import Sources from "./features/Sources";
import Compare from "./features/Compare";
import Brands from "./features/Brands";

function RootRedirect() {
  const { data: brands, isLoading } = useBrands();
  if (isLoading) return <Shell><Spinner /></Shell>;
  if (brands && brands.length) return <Navigate to={`/brand/${brands[0].id}/overview`} replace />;
  return <Navigate to="/brands" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/brands" element={<Shell><Brands /></Shell>} />
      <Route path="/compare" element={<Shell><Compare /></Shell>} />
      <Route path="/brand/:brandId/overview" element={<Shell><Overview /></Shell>} />
      <Route path="/brand/:brandId/sales" element={<Shell><Sales /></Shell>} />
      <Route path="/brand/:brandId/marketing" element={<Shell><Marketing /></Shell>} />
      <Route path="/brand/:brandId/creators" element={<Shell><Creators /></Shell>} />
      <Route path="/brand/:brandId/voice" element={<Shell><Voice /></Shell>} />
      <Route path="/brand/:brandId/web" element={<Shell><Web /></Shell>} />
      <Route path="/brand/:brandId/sources" element={<Shell><Sources /></Shell>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
