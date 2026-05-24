/**
 * App root (spec §31.2). The instrument-panel shell: a sidebar nav over the 18
 * views with the active view derived from state (no useEffect — per the project
 * React rules). The window launches directly into Today.
 */
import { useState } from "react";
import { Layout } from "./components/Layout";
import { VIEWS } from "./views";

export function App() {
  const [activeId, setActiveId] = useState<string>(VIEWS[0].id);
  const active = VIEWS.find((v) => v.id === activeId) ?? VIEWS[0];
  const ActiveView = active.Component;

  return (
    <Layout views={VIEWS} activeId={activeId} onSelect={setActiveId}>
      <ActiveView />
    </Layout>
  );
}
