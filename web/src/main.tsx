import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./index.css";

const client = new QueryClient({
  defaultOptions: {
    queries: {
      // A live fund changes once a day. Polling every thirty seconds keeps a left-open
      // tab honest without pretending this is a trading terminal.
      refetchInterval: 30_000,
      staleTime: 15_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
