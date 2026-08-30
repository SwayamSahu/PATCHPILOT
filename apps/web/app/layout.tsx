import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PatchPilot",
  description: "From production incident to verified fix — with a human in control.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // Browser extensions - Grammarly, password managers, translation tools -
    // inject attributes into <html> and <body> before React hydrates, which
    // React then reports as a server/client mismatch. It is not a bug in this
    // page and there is nothing to repair, so the warning is suppressed at the
    // two elements extensions actually touch. It is deliberately not applied
    // deeper, where a mismatch would mean something real.
    <html lang="en" suppressHydrationWarning>
      <body className="font-mono antialiased" suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
