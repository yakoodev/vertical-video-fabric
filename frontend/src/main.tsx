import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { queryClient } from "@/lib/queryClient";
import { App } from "@/App";
import { watchMediaRelease } from "@/lib/releaseMedia";
import { ToastProvider } from "@/components/Toast";
import "@/styles/fonts.css";
import "@/styles/global.css";
import "@/styles/shell.css";
import "@/styles/clip.css";

watchMediaRelease();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
