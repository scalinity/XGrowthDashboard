/**
 * App root (spec §31.2). The instrument-panel shell: a sidebar nav over the 18
 * views with the active view derived from state (no useEffect — per the project
 * React rules). The window launches directly into Today.
 */
import { useCallback, useState } from "react";
import { Layout } from "./components/Layout";
import { NavContext, NavParamsContext, type NavParams } from "./lib/nav";
import { VIEWS } from "./views";

export function App() {
  const [activeId, setActiveId] = useState<string>(VIEWS[0].id);
  const [navParams, setNavParams] = useState<NavParams>({});
  const active = VIEWS.find((v) => v.id === activeId) ?? VIEWS[0];
  const ActiveView = active.Component;

  const navigate = useCallback((viewId: string, params?: NavParams) => {
    setActiveId(viewId);
    setNavParams(params ?? {});
  }, []);

  const handleSidebarSelect = useCallback((viewId: string) => {
    setActiveId(viewId);
    setNavParams({});
  }, []);

  return (
    <NavContext.Provider value={navigate}>
      <NavParamsContext.Provider value={navParams}>
        <Layout views={VIEWS} activeId={activeId} onSelect={handleSidebarSelect}>
          <ActiveView />
        </Layout>
      </NavParamsContext.Provider>
    </NavContext.Provider>
  );
}
