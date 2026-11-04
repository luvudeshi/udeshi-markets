import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { ApiProvider } from "@/lib/api-context";

// Rendered per request, so UDESHI_API_URL is read when the server runs, not when it is built:
// one image works against any backend.
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Udeshi Markets",
  description: "Trading terminal for a simulated electronic exchange",
};

export const viewport: Viewport = {
  themeColor: "#07090c",
  colorScheme: "dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  const apiUrl = process.env.UDESHI_API_URL ?? "http://127.0.0.1:8000";
  return (
    <html lang="en">
      <body>
        <ApiProvider baseUrl={apiUrl}>{children}</ApiProvider>
      </body>
    </html>
  );
}
