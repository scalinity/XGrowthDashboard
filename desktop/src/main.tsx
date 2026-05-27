import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Bundled fonts (offline-identical rendering — §31.4). Fraunces is the variable
// font (all weights via the axis); Plex Sans + JetBrains Mono ship the weights
// theme.py uses.
import "@fontsource-variable/fraunces";
import "@fontsource/ibm-plex-sans/300.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "@fontsource/jetbrains-mono/600.css";

import "./theme/tokens.css";
import { App } from "./App";
import { SidecarBootstrap } from "./components/SidecarBootstrap";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing");

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000 } },
});

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <SidecarBootstrap>
        <App />
      </SidecarBootstrap>
    </QueryClientProvider>
  </StrictMode>,
);
