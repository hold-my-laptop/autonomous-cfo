import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { TodayPage } from "./features/today/TodayPage";
import { ActivityPage } from "./features/audit/ActivityPage";
import { ContractPage } from "./features/contract/ContractPage";
import { VeroPage } from "./features/vero/VeroPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<TodayPage />} />
          <Route path="contract" element={<ContractPage />} />
          <Route path="costs" element={<VeroPage />} />
          <Route path="activity" element={<ActivityPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
